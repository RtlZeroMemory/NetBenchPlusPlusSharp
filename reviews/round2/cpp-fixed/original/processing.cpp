#include "bench.hpp"

namespace bench {
void Epoch::check(const Result& r,size_t n) const {
    require(r.records<=1000000000ull-records,"epoch_record_limit");
    require(batches!=UINT64_MAX && n<=UINT64_MAX-bytes,"epoch_counter_overflow");
}
void Epoch::add(const Result& r,size_t n,uint64_t seq) {
    check(r,n); ++batches; bytes+=n; records+=r.records;
    hash_int(digest,seq,8);hash_int(digest,r.records,8);hash_int(digest,r.digest,8);
    for(size_t i=0;i<64;++i){counts[i]+=r.counts[i];sums[i]+=r.sums[i];}
}
std::array<uint8_t,summary_size> Epoch::encode() const {
    std::array<uint8_t,summary_size> b{};
    write_be(b.data(),batches,8);write_be(b.data()+8,bytes,8);write_be(b.data()+16,records,8);write_be(b.data()+24,digest,8);
    for(size_t i=0;i<64;++i){write_be(b.data()+32+8*i,counts[i],8);write_be(b.data()+544+8*i,uint64_t(sums[i]),8);}return b;
}
static void add_row(Result& r,const Row& row,std::string_view message) {
    require(r.records<1000000000,"batch_record_limit"); ++r.records;
    size_t bucket=size_t(row.kind)*4+(row.flags&3);++r.counts[bucket];r.sums[bucket]+=row.value;
    hash_byte(r.digest,0x52);hash_int(r.digest,row.id,8);hash_int(r.digest,row.timestamp,8);
    hash_int(r.digest,row.source,4);hash_byte(r.digest,row.kind);hash_int(r.digest,uint64_t(row.value),8);
    hash_int(r.digest,row.flags,4);hash_int(r.digest,message.size(),4);
    for(unsigned char c:message)hash_byte(r.digest,c);
}
static void integer_token(simdjson::ondemand::value& v,bool is_signed) {
    std::string_view s=v.raw_json_token();
    while(!s.empty() && (s.back()==' '||s.back()=='\t'||s.back()=='\r'||s.back()=='\n'))s.remove_suffix(1);
    require(!s.empty(),"integer_token");
    if(s.front()=='-'){require(is_signed,"unsigned_minus");s.remove_prefix(1);}
    require(!s.empty() && (s.size()==1||s.front()!='0'),"integer_token");
    for(char c:s)require(c>='0'&&c<='9',"integer_token");
}
Processor::Processor(const Options& o):mode(o.mode),cap_bytes(o.retain_bytes),cap_batches(o.retain_batches),slots(o.mode.starts_with("retain")?o.retain_batches+1:1) {
    if(mode!="transport") require(parser.allocate(o.max_frame,4)==simdjson::SUCCESS,"parser_capacity");
    live.reserve(cap_batches);
}
Result Processor::parse(const uint8_t* data,size_t length,size_t capacity,bool sample) {
    int64_t decode_start=sample?now():0;
    require(length>0,"empty_json");
    require(!(length>=3 && data[0]==0xef && data[1]==0xbb && data[2]==0xbf),"json_bom");
    bool retain=mode.starts_with("retain");auto& batch=slots[scratch];batch.clear();Result r;
    simdjson::ondemand::document doc=parser.iterate(data,length,capacity);
    for(auto item:doc.get_array()) {
        Row row;std::string_view message;unsigned seen=0;
        for(auto field:item.get_object()) {
            std::string_view key=field.unescaped_key();
            unsigned k=0;
            if(key=="id")k=1;else if(key=="timestamp_ns")k=2;else if(key=="source")k=4;else if(key=="kind")k=8;
            else if(key=="value_milli")k=16;else if(key=="flags")k=32;else if(key=="message")k=64;
            require(k && !(seen&k),"unknown_or_duplicate_property");seen|=k;
            simdjson::ondemand::value v=field.value();
            if(k==8) {
                std::string_view kind=v.get_string();require(kind.size()==6 && kind.substr(0,4)=="kind" && kind[4]>='0' && kind[4]<='1' && kind[5]>='0' && kind[5]<='9',"kind");
                row.kind=uint8_t((kind[4]-'0')*10+kind[5]-'0');require(row.kind<16,"kind");
            } else if(k==64) {message=v.get_string();require(message.size()<=4096,"message_length");}
            else {
                integer_token(v,k==16);
                if(k==16){row.value=v.get_int64();require(row.value>=-1000000&&row.value<=1000000,"value_range");}
                else {uint64_t n=v.get_uint64(); if(k==1)row.id=n;else if(k==2)row.timestamp=n;else {require(n<=UINT32_MAX,"u32_range");if(k==4)row.source=uint32_t(n);else row.flags=uint32_t(n);}}
            }
        }
        require(seen==127,"missing_property");
        if(retain) {
            require(batch.canonical<=cap_bytes && 38+message.size()<=cap_bytes-batch.canonical,"retention_capacity");
            require(batch.text.size()<=UINT32_MAX-message.size(),"retention_offset");
            row.begin=uint32_t(batch.text.size());row.length=uint32_t(message.size());
            batch.rows.push_back(row);batch.text.insert(batch.text.end(),message.begin(),message.end());batch.canonical+=38+message.size();
        } else add_row(r,row,message);
    }
    require(doc.at_end(),"trailing_json");
    int64_t decoded=sample?now():0;if(sample){last_decode_ns=nanoseconds(decoded-decode_start);last_visit_ns=0;}
    if(retain){r=visit(batch);batch.digest=r.digest;if(sample)last_visit_ns=nanoseconds(now()-decoded);} return r;
}
Result Processor::visit(const Batch& b) const {
    Result r;
    for(const auto& row:b.rows){require(size_t(row.begin)+row.length<=b.text.size(),"retained_bounds");
        const char* p=b.text.empty()?"":reinterpret_cast<const char*>(b.text.data()+row.begin);
        add_row(r,row,std::string_view(p,row.length));}return r;
}
Result Processor::apply(const uint8_t* data,size_t length,size_t capacity,Epoch& epoch,uint64_t seq,bool sample) {
    Result r;
    if(mode=="transport")r.digest=length;
    else r=parse(data,length,capacity,sample);
    epoch.check(r,length);
    if(mode.starts_with("retain")) {
        const uint64_t incoming=slots[scratch].canonical;
        // All revisits precede mutations, so invalid data or lifetime corruption cannot partly evict.
        int64_t revisit_start=sample?now():0;
        size_t remove=0;uint64_t remaining=retained;
        while(live.size()-remove>=cap_batches || incoming>cap_bytes-remaining) {
            const auto& b=slots[live[remove++]];require(visit(b).digest==b.digest,"retained_digest");remaining-=b.canonical;
        }
        if(sample)last_visit_ns+=nanoseconds(now()-revisit_start);
        for(size_t i=0;i<remove;++i)if(mode=="retain-allocate")slots[live[i]]=Batch{};
        live.erase(live.begin(),live.begin()+remove);live.push_back(scratch);retained=remaining+incoming;peak_retained=std::max(peak_retained,retained);
        for(size_t i=0;i<slots.size();++i)if(std::find(live.begin(),live.end(),i)==live.end()){scratch=i;break;}
        if(mode=="retain-allocate")slots[scratch]=Batch{};
    }
    epoch.add(r,length,seq);return r;
}
void Processor::verify() const {for(size_t i:live)require(visit(slots[i]).digest==slots[i].digest,"retained_digest");}
size_t Processor::owned_capacity() const {size_t n=0;for(const auto& s:slots)n+=s.rows.capacity()*sizeof(Row)+s.text.capacity();return n;}

std::vector<Frame> load_corpus(const std::string& path) {
    std::ifstream f(path,std::ios::binary|std::ios::ate);require(bool(f),"corpus_open");auto length=std::streamoff(f.tellg());require(length>=16,"corpus_header");f.seekg(0);
    auto read=[&](uint8_t* p,size_t n){require(n<=size_t(std::numeric_limits<std::streamsize>::max()),"corpus_size");require(bool(f.read(reinterpret_cast<char*>(p),std::streamsize(n))),"corpus_truncated");};
    std::array<uint8_t,16> h{};read(h.data(),h.size());require(std::memcmp(h.data(),"TCPBCH01",8)==0 && read_be(h.data()+12,4)==0,"corpus_header");
    uint64_t count=read_be(h.data()+8,4);require(count>0 && count<=uint64_t(length-16)/1045,"corpus_count");
    std::vector<Frame> frames;frames.reserve(size_t(count));uint64_t consumed=16;
    for(uint64_t i=0;i<count;++i) {
        std::array<uint8_t,1044> meta{};read(meta.data(),meta.size());consumed+=meta.size();Frame frame;
        frame.length=size_t(read_be(meta.data(),4));require(frame.length>0 && frame.length<=hard_limit && frame.length<=uint64_t(length)-consumed,"corpus_length");
        frame.expected.records=read_be(meta.data()+4,8);frame.expected.digest=read_be(meta.data()+12,8);require(frame.expected.records<=1000000000,"corpus_records");
        uint64_t sum=0;for(size_t j=0;j<64;++j){auto c=read_be(meta.data()+20+8*j,8);require(c<=frame.expected.records-sum,"corpus_counts");sum+=c;frame.expected.counts[j]=c;
            int64_t s=std::bit_cast<int64_t>(read_be(meta.data()+532+8*j,8));require(s>=-int64_t(c)*1000000 && s<=int64_t(c)*1000000,"corpus_sums");frame.expected.sums[j]=s;}
        require(sum==frame.expected.records,"corpus_counts");frame.payload.resize(frame.length+simdjson::SIMDJSON_PADDING);read(frame.payload.data(),frame.length);consumed+=frame.length;frames.push_back(std::move(frame));
    }
    require(consumed==uint64_t(length),"corpus_trailing");return frames;
}

void selftest() {
    const std::string good=R"([{"id":18446744073709551615,"timestamp_ns":0,"source":4294967295,"kind":"kind15","value_milli":-0,"flags":3,"message":"a\u0000\uD83D\uDE00"}])";
    Row row;row.id=UINT64_MAX;row.source=UINT32_MAX;row.kind=15;row.flags=3;Result reference;add_row(reference,row,std::string_view("a\0\xf0\x9f\x98\x80",6));
    for(const auto& mode:{"aggregate","retain-reuse","retain-allocate"}) {
        Options o;o.mode=mode;o.retain_batches=2;Processor p(o);Epoch epoch;
        auto run=[&](std::string s){Bytes b(s.begin(),s.end());b.resize(s.size()+simdjson::SIMDJSON_PADDING);auto r=p.apply(b.data(),s.size(),b.size(),epoch,epoch.batches+1);std::fill(b.begin(),b.end(),uint8_t(0xcd));return r;};
        require(run(good)==reference,"selftest_canonical");
        std::vector<std::string> bad={"", " ","{}","[] []","[],","[[]]","[{}]",good+"x",std::string("\xef\xbb\xbf")+good};
        for(auto [a,b]:std::vector<std::pair<std::string,std::string>>{{"18446744073709551615","18446744073709551616"},{"18446744073709551615","-0"},{"4294967295","4294967296"},{"\"value_milli\":-0","\"value_milli\":1e0"},{"\"value_milli\":-0","\"value_milli\":1.0"},{"\"value_milli\":-0","\"value_milli\":1000001"},{"kind15","kind16"},{"a\\u0000\\uD83D\\uDE00","\\uD800"},{"a\\u0000\\uD83D\\uDE00",std::string("\xc0\xaf")},{"\"source\":","\"id\":"},{"\"source\":","\"unknown\":"}}) {
            auto s=good;s.replace(s.find(a),a.size(),b);bad.push_back(s);
        }
        for(auto& s:bad){auto before=epoch.encode();bool threw=false;try{run(s);}catch(...){threw=true;}require(threw && before==epoch.encode(),"selftest_atomic_invalid");p.verify();}
        for(int i=0;i<16;++i)require(run(good)==reference,"selftest_reuse");p.verify();
        require(run("[]")==Result{},"selftest_empty");
        auto escaped_key=good;escaped_key.replace(escaped_key.find("\"id\""),4,"\"\\u0069d\"");require(run(escaped_key)==reference,"selftest_escaped_key");
    }
    for(auto v:{0ull,1ull,1023ull,1024ull,1027ull,2048ull,1000000ull,60000000000ull,UINT64_MAX})require(Histogram::upper(Histogram::index(v))>=v,"selftest_histogram");
    std::cout<<"{\"event\":\"selftest\",\"ok\":true,\"canonical_digest\":"<<reference.digest<<"}\n";
}
}
