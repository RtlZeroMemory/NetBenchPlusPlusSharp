using System.Buffers.Binary;
using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Unicode;

namespace TcpBench;

enum Mode
{
    Aggregate,
    RetainReuse,
    RetainAllocate,
    Transport
}

// Cooperative processing budget: shutdown plus the earlier of the frame/idle deadlines.
// Default (deadline 0, no token) is unlimited, for selftests and processing-only controls.
readonly struct Budget(long deadline, CancellationToken stop, string reason = "frame_timeout")
{
    public void Check()
    {
        if (stop.IsCancellationRequested)
        {
            throw new BenchException("shutdown");
        }

        if (deadline != 0 && Stopwatch.GetTimestamp() >= deadline)
        {
            throw new BenchException(reason);
        }
    }

    // Diagnostic delay as a budget-checked busy spin, so sub-millisecond pauses are honoured.
    public void Spin(int milliseconds)
    {
        long finish = Stopwatch.GetTimestamp() + milliseconds * Stopwatch.Frequency / 1000;
        while (Stopwatch.GetTimestamp() < finish)
        {
            Check();
            Thread.SpinWait(1);
        }

        Check();
    }
}

// A protocol, validation or accounting failure with a short, stable reason code.
sealed class BenchException(string reason) : Exception(reason);

static class Digest
{
    public const ulong Offset = 14695981039346656037UL;
    const ulong Prime = 1099511628211UL;

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public static ulong Byte(ulong h, byte b) => unchecked((h ^ b) * Prime);

    // Big-endian bytes of v. A plain loop measured ~20% faster overall than a force-inlined
    // unrolled form, which pushed the parser past the JIT's inlining budget.
    public static ulong Number(ulong h, ulong v, int width)
    {
        for (int i = width - 1; i >= 0; --i)
        {
            h = Byte(h, (byte)(v >> (i * 8)));
        }

        return h;
    }

    public static ulong Row(ulong h, in Row row, ReadOnlySpan<byte> message)
    {
        h = Byte(h, 0x52);
        h = Number(h, row.Id, 8);
        h = Number(h, row.Timestamp, 8);
        h = Number(h, row.Source, 4);
        h = Byte(h, row.Kind);
        h = Number(h, unchecked((ulong)row.Value), 8);
        h = Number(h, row.Flags, 4);
        h = Number(h, (uint)message.Length, 4);
        foreach (byte b in message)
        {
            h = Byte(h, b);
        }

        return h;
    }
}

// Owned, canonical record. Offset (chunk * 65,536 + position) and Length index the batch's own text.
struct Row
{
    public ulong Id, Timestamp;
    public long Value;
    public uint Source, Flags;
    public byte Kind;
    public int Offset, Length;
}

sealed class Batch
{
    // Identical chunked storage in both implementations, so no array reaches the large-object heap
    // (85,000 bytes): the first chunk grows geometrically from 16 rows / 256 bytes, later chunks are
    // allocated full (1,024 rows = 48 KiB, 64 KiB of text). A message (at most 4 KiB) never straddles.
    const int RowChunk = 1024, TextChunk = 65536;
    readonly List<Row[]> rows = [];
    readonly List<byte[]> text = [];
    int textChunk, textUsed;
    public int Count;
    public long Canonical;
    public ulong Digest;

    public long Capacity
    {
        get
        {
            long n = 0;
            foreach (Row[] r in rows)
            {
                n += 48L * r.Length;
            }

            foreach (byte[] t in text)
            {
                n += t.Length;
            }

            return n;
        }
    }

    public void Clear()
    {
        Count = textChunk = textUsed = 0;
        Canonical = 0;
    }

    public void Add(in Row row, ReadOnlySpan<byte> message, long limit)
    {
        if (Canonical > limit || 38 + message.Length > limit - Canonical)
        {
            throw new BenchException("retention_capacity");
        }

        int chunk = Count / RowChunk, slot = Count % RowChunk;
        if (chunk == rows.Count)
        {
            rows.Add(GC.AllocateUninitializedArray<Row>(chunk == 0 ? 16 : RowChunk));
        }
        else if (slot == rows[chunk].Length)
        {
            rows[chunk] = Grow(rows[chunk], slot, slot * 2);
        }

        if (text.Count == 0)
        {
            text.Add(GC.AllocateUninitializedArray<byte>(Math.Max(256, message.Length)));
        }
        else if (textUsed + message.Length > text[textChunk].Length)
        {
            byte[] current = text[textChunk];
            if (current.Length < TextChunk && textUsed + message.Length <= TextChunk)
            {
                text[textChunk] = Grow(current, textUsed, Math.Min(TextChunk, Math.Max(current.Length * 2, textUsed + message.Length)));
            }
            else
            {
                textChunk++;
                textUsed = 0;
                if (textChunk == text.Count)
                {
                    text.Add(GC.AllocateUninitializedArray<byte>(TextChunk));
                }
            }
        }

        ref Row owned = ref rows[chunk][slot];
        owned = row;
        owned.Offset = textChunk * TextChunk + textUsed;
        owned.Length = message.Length;
        message.CopyTo(text[textChunk].AsSpan(textUsed));
        textUsed += message.Length;
        Count++;
        Canonical += 38 + message.Length;
    }

    // Uninitialized like std::vector::reserve; only the used prefix is ever read.
    static T[] Grow<T>(T[] old, int used, int size)
    {
        T[] grown = GC.AllocateUninitializedArray<T>(size);
        old.AsSpan(0, used).CopyTo(grown);
        return grown;
    }

    public void Visit(Result result, Budget budget)
    {
        result.Clear();
        for (int i = 0; i < Count; i++)
        {
            if ((i & 1023) == 0)
            {
                budget.Check();
            }

            ref Row row = ref rows[i / RowChunk][i % RowChunk];
            result.Add(row, text[row.Offset / TextChunk].AsSpan(row.Offset % TextChunk, row.Length));
        }

        budget.Check();
    }
}

// Per-batch result: record count, canonical digest and the 64 category buckets.
sealed class Result
{
    public ulong Records, Hash = Digest.Offset;
    public readonly ulong[] Counts = new ulong[64];
    public readonly long[] Sums = new long[64];

    public void Clear()
    {
        Records = 0;
        Hash = Digest.Offset;
        Array.Clear(Counts);
        Array.Clear(Sums);
    }

    public void Add(in Row row, ReadOnlySpan<byte> message)
    {
        if (Records == Summary.RecordLimit)
        {
            throw new BenchException("epoch_record_limit");
        }

        Records++;
        int bucket = row.Kind * 4 + (int)(row.Flags & 3);
        Counts[bucket]++;
        Sums[bucket] += row.Value;
        Hash = Digest.Row(Hash, row, message);
    }

    public bool Matches(ulong records, ulong hash, ReadOnlySpan<ulong> counts, ReadOnlySpan<long> sums) =>
        Records == records && Hash == hash && Counts.AsSpan().SequenceEqual(counts) && Sums.AsSpan().SequenceEqual(sums);
}

// Per-connection epoch summary returned by End.
sealed class Summary
{
    public const ulong RecordLimit = 1_000_000_000;
    public static readonly ulong[] NoCounts = new ulong[64];
    public static readonly long[] NoSums = new long[64];
    public ulong Batches, Bytes, Records, Hash = Digest.Offset;
    public readonly ulong[] Counts = new ulong[64];
    public readonly long[] Sums = new long[64];

    public void Clear()
    {
        Batches = Bytes = Records = 0;
        Hash = Digest.Offset;
        Array.Clear(Counts);
        Array.Clear(Sums);
    }

    public void Check(ulong records)
    {
        if (records > RecordLimit - Records)
        {
            throw new BenchException("epoch_record_limit");
        }
    }

    public void Add(ulong sequence, int bytes, ulong records, ulong hash, ReadOnlySpan<ulong> counts, ReadOnlySpan<long> sums)
    {
        Check(records);
        Batches++;
        Bytes += (ulong)bytes;
        Records += records;
        Hash = Digest.Number(Digest.Number(Digest.Number(Hash, sequence, 8), records, 8), hash, 8);
        for (int i = 0; i < 64; i++)
        {
            Counts[i] += counts[i];
            Sums[i] += sums[i];
        }
    }

    public void Write(Span<byte> output)
    {
        BinaryPrimitives.WriteUInt64BigEndian(output, Batches);
        BinaryPrimitives.WriteUInt64BigEndian(output[8..], Bytes);
        BinaryPrimitives.WriteUInt64BigEndian(output[16..], Records);
        BinaryPrimitives.WriteUInt64BigEndian(output[24..], Hash);
        for (int i = 0; i < 64; i++)
        {
            BinaryPrimitives.WriteUInt64BigEndian(output[(32 + i * 8)..], Counts[i]);
            BinaryPrimitives.WriteInt64BigEndian(output[(544 + i * 8)..], Sums[i]);
        }
    }

    public bool Matches(ReadOnlySpan<byte> body)
    {
        Span<byte> expected = stackalloc byte[1056];
        Write(expected);
        return body.SequenceEqual(expected);
    }
}

sealed class Processor
{
    readonly Mode mode;
    readonly int retainBatches;
    readonly long retainBytes;
    readonly Queue<Batch> live = new();
    readonly Stack<Batch> spare = new(); // reuse mode: evicted storage awaiting reuse
    public readonly Summary Epoch = new();
    public readonly Result Last = new();
    readonly Result check = new(); // retained-batch revisits
    readonly byte[] messageScratch = new byte[4096]; // decoded escaped messages (the length limit)
    public long Retained { get; private set; }
    public long PeakRetained { get; private set; }
    public long PeakOwned { get; private set; }
    public long LastDecodeTicks { get; private set; }
    public long LastVisitTicks { get; private set; }

    public Processor(Mode mode, int retainBatches, long retainBytes)
    {
        this.mode = mode;
        this.retainBatches = retainBatches;
        this.retainBytes = retainBytes;
    }

    internal Batch[] LiveBatches() => [.. live]; // selftest hook

    public long OwnedCapacity(Batch? scratch = null)
    {
        long bytes = scratch?.Capacity ?? 0;
        foreach (Batch b in live)
        {
            bytes += b.Capacity;
        }

        foreach (Batch b in spare)
        {
            bytes += ReferenceEquals(b, scratch) ? 0 : b.Capacity;
        }

        return bytes;
    }

    // Validates, processes and commits one batch, or throws with no committed change.
    public Result Process(ReadOnlySpan<byte> payload, ulong sequence, Budget budget = default, bool sample = false)
    {
        budget.Check();
        long start = sample ? Stopwatch.GetTimestamp() : 0;
        Result r = Last;
        if (mode is Mode.Aggregate or Mode.Transport)
        {
            if (mode == Mode.Transport)
            {
                r.Clear();
                r.Hash = (ulong)payload.Length;
            }
            else
            {
                Parse(payload, null, r, budget);
            }

            if (sample)
            {
                LastDecodeTicks = Stopwatch.GetTimestamp() - start;
                LastVisitTicks = 0;
            }

            Epoch.Check(r.Records);
            budget.Check();
            Epoch.Add(sequence, payload.Length, r.Records, r.Hash, r.Counts, r.Sums);
            return r;
        }

        // Scratch is never committed state; allocate mode always starts from fresh arrays.
        Batch scratch = mode == Mode.RetainReuse && spare.Count > 0 ? spare.Peek() : new Batch();
        scratch.Clear();
        try
        {
            Parse(payload, scratch, r, budget);
        }
        finally
        {
            // Committed batches plus incoming scratch, including a failed parse's growth.
            PeakOwned = Math.Max(PeakOwned, OwnedCapacity(scratch));
        }

        long decoded = sample ? Stopwatch.GetTimestamp() : 0;
        scratch.Visit(r, budget);
        scratch.Digest = r.Hash;
        // Verify every planned eviction before any committed state changes.
        int evict = 0;
        long remaining = Retained;
        foreach (Batch old in live)
        {
            if (live.Count - evict < retainBatches && scratch.Canonical <= retainBytes - remaining)
            {
                break;
            }

            old.Visit(check, budget);
            if (check.Hash != old.Digest)
            {
                throw new BenchException("retained_integrity");
            }

            remaining -= old.Canonical;
            evict++;
        }

        Epoch.Check(r.Records);
        budget.Check();
        // Commit: no failure point from here on.
        if (spare.Count > 0 && ReferenceEquals(spare.Peek(), scratch))
        {
            spare.Pop();
        }

        for (int i = 0; i < evict; i++)
        {
            Batch old = live.Dequeue();
            if (mode == Mode.RetainReuse)
            {
                spare.Push(old);
            }
        }

        live.Enqueue(scratch);
        Retained = remaining + scratch.Canonical;
        PeakRetained = Math.Max(PeakRetained, Retained);
        Epoch.Add(sequence, payload.Length, r.Records, r.Hash, r.Counts, r.Sums);
        if (sample)
        {
            LastDecodeTicks = decoded - start;
            LastVisitTicks = Stopwatch.GetTimestamp() - decoded;
        }

        return r;
    }

    public void VerifyRetained(Budget budget = default)
    {
        budget.Check();
        foreach (Batch b in live)
        {
            b.Visit(check, budget);
            if (check.Hash != b.Digest)
            {
                throw new BenchException("retained_integrity");
            }
        }
    }

    void Parse(ReadOnlySpan<byte> input, Batch? batch, Result r, Budget budget)
    {
        r.Clear();
        // Defaults reject comments and trailing commas; depth 4 with the root array at depth 1.
        var reader = new Utf8JsonReader(input, new JsonReaderOptions { MaxDepth = 4 });
        if (!reader.Read() || reader.TokenType != JsonTokenType.StartArray)
        {
            throw new BenchException("json_root");
        }

        Span<byte> nameScratch = stackalloc byte[16];
        Span<byte> messageScratch = this.messageScratch; // reused; stackalloc would re-zero 4 KiB per frame
        ulong parsed = 0;
        while (reader.Read() && reader.TokenType != JsonTokenType.EndArray)
        {
            if ((parsed++ & 1023) == 0)
            {
                budget.Check();
            }

            if (reader.TokenType != JsonTokenType.StartObject)
            {
                throw new BenchException("json_record");
            }

            Row row = default;
            scoped ReadOnlySpan<byte> message = default;
            int seen = 0;
            while (reader.Read() && reader.TokenType != JsonTokenType.EndObject)
            {
                // Names compare decoded, so escapes cannot hide a duplicate or unknown field. An unescaped
                // name equal to a field name is plain ASCII: match it in place, else decode and validate.
                int bit = reader.ValueIsEscaped ? 0 : FieldBit(reader.ValueSpan);
                if (bit == 0)
                {
                    bit = FieldBit(nameScratch[..Copy(ref reader, nameScratch, "json_fields")]);
                }

                if (bit == 0 || (seen & bit) != 0 || !reader.Read())
                {
                    throw new BenchException("json_fields");
                }

                seen |= bit;
                switch (bit)
                {
                    case 1:
                        row.Id = Unsigned(ref reader, ulong.MaxValue);
                        break;
                    case 2:
                        row.Timestamp = Unsigned(ref reader, ulong.MaxValue);
                        break;
                    case 4:
                        row.Source = (uint)Unsigned(ref reader, uint.MaxValue);
                        break;
                    case 8:
                        int kind = reader.TokenType == JsonTokenType.String && !reader.ValueIsEscaped ? Kind(reader.ValueSpan) : -1;
                        if (kind < 0)
                        {
                            kind = reader.TokenType == JsonTokenType.String ? Kind(nameScratch[..Copy(ref reader, nameScratch, "json_kind")]) : -1;
                        }

                        row.Kind = kind >= 0 ? (byte)kind : throw new BenchException("json_kind");
                        break;
                    case 16:
                        row.Value = Signed(ref reader);
                        break;
                    case 32:
                        row.Flags = (uint)Unsigned(ref reader, uint.MaxValue);
                        break;
                    default:
                        if (reader.TokenType != JsonTokenType.String)
                        {
                            throw new BenchException("json_message");
                        }

                        // Without escapes the raw bytes are the decoded text. Read() checks neither UTF-8 nor
                        // surrogate pairing, so both are checked here on the decoded bytes.
                        message = reader.ValueIsEscaped ? messageScratch[..Unescape(reader.ValueSpan, messageScratch)] : reader.ValueSpan;
                        if (!Utf8.IsValid(message))
                        {
                            throw new BenchException("json_string");
                        }

                        if (message.Length > 4096)
                        {
                            throw new BenchException("json_message_length");
                        }
                        break;
                }
            }

            if (seen != 127)
            {
                throw new BenchException("json_fields");
            }

            if (batch != null)
            {
                batch.Add(row, message, retainBytes);
            }
            else
            {
                r.Add(row, message);
            }
        }

        if (reader.TokenType != JsonTokenType.EndArray || reader.Read())
        {
            throw new BenchException("json_trailing");
        }
    }

    static int FieldBit(ReadOnlySpan<byte> name) => name.Length switch
    {
        2 when name.SequenceEqual("id"u8) => 1,
        12 when name.SequenceEqual("timestamp_ns"u8) => 2,
        6 when name.SequenceEqual("source"u8) => 4,
        4 when name.SequenceEqual("kind"u8) => 8,
        11 when name.SequenceEqual("value_milli"u8) => 16,
        5 when name.SequenceEqual("flags"u8) => 32,
        7 when name.SequenceEqual("message"u8) => 64,
        _ => 0
    };

    // Decodes a string token that Read() has already syntax-checked (every escape is one of \" \\ \/ \b
    // \f \n \r \t or \u with four hex digits). Measured about 20% faster overall on HARD content than
    // CopyString, which parses each \u escape generically. Rejects lone surrogates; the caller checks UTF-8.
    // Returns the decoded length, or throws json_message_length when it would exceed the destination.
    public static int Unescape(ReadOnlySpan<byte> source, Span<byte> destination)
    {
        int written = 0, i = 0;
        while (true)
        {
            // Escapes often follow each other (\u00e9\u00e8...): only search when the next byte is plain.
            int plain = i < source.Length && source[i] == '\\' ? 0 : source[i..].IndexOf((byte)'\\');
            plain = plain < 0 ? source.Length - i : plain;
            if (plain > destination.Length - written)
            {
                throw new BenchException("json_message_length");
            }

            source.Slice(i, plain).CopyTo(destination[written..]);
            written += plain;
            i += plain;
            if (i == source.Length)
            {
                return written;
            }

            byte kind = source[i + 1];
            int code;
            if (kind != 'u')
            {
                code = kind switch { (byte)'b' => 8, (byte)'f' => 12, (byte)'n' => 10, (byte)'r' => 13, (byte)'t' => 9, _ => kind };
                i += 2;
            }
            else
            {
                code = Hex4(source.Slice(i + 2, 4));
                i += 6;
                if (code is >= 0xD800 and <= 0xDBFF)
                {
                    int low = source.Length - i >= 6 && source[i] == '\\' && source[i + 1] == 'u' ? Hex4(source.Slice(i + 2, 4)) : 0;
                    if (low is < 0xDC00 or > 0xDFFF)
                    {
                        throw new BenchException("json_string");
                    }

                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                    i += 6;
                }
                else if (code is >= 0xDC00 and <= 0xDFFF)
                {
                    throw new BenchException("json_string");
                }
            }

            if (code < 0x80)
            {
                if (written == destination.Length)
                {
                    throw new BenchException("json_message_length");
                }

                destination[written++] = (byte)code;
                continue;
            }

            int length = code < 0x800 ? 2 : code < 0x10000 ? 3 : 4;
            if (length > destination.Length - written)
            {
                throw new BenchException("json_message_length");
            }

            // Lead byte, then continuation bytes from the most significant six bits down.
            destination[written] = (byte)((0xF00 >> length) | (code >> (6 * (length - 1))));
            for (int k = 1; k < length; k++)
            {
                destination[written + k] = (byte)(0x80 | ((code >> (6 * (length - 1 - k))) & 0x3F));
            }

            written += length;
        }
    }

    // Four hex digits already validated by Read(): '0'-'9' map through the low nibble, 'A'-'F'/'a'-'f' add 9.
    static int Hex4(ReadOnlySpan<byte> s) =>
        (Hex(s[0]) << 12) | (Hex(s[1]) << 8) | (Hex(s[2]) << 4) | Hex(s[3]);

    static int Hex(byte c) => (c & 0xF) + (c >> 6) * 9;

    static int Kind(ReadOnlySpan<byte> k) =>
        k.Length != 6 || !k.StartsWith("kind"u8) || k[4] is not ((byte)'0' or (byte)'1') || k[5] < '0' || k[5] > '9' || (k[4] == '1' && k[5] > '5')
            ? -1
            : (k[4] - '0') * 10 + k[5] - '0';

    // Read() validates neither string UTF-8 nor surrogate escapes; CopyString unescapes, rejects
    // lone surrogates and invalid UTF-8, and fails when the decoded text exceeds the scratch.
    // Copying every string measured faster than in-place spans plus Utf8.IsValid.
    static int Copy(ref Utf8JsonReader reader, scoped Span<byte> scratch, string tooLong)
    {
        try
        {
            return reader.CopyString(scratch);
        }
        catch (ArgumentException)
        {
            throw new BenchException(tooLong);
        }
        catch (InvalidOperationException)
        {
            throw new BenchException("json_string");
        }
    }

    // Strict JSON integer lexeme: digits only, no leading zero, no fraction or exponent.
    // Only a 20th digit can overflow 64 bits.
    static bool Digits(ReadOnlySpan<byte> s, out ulong value)
    {
        value = 0;
        if (s.IsEmpty || s.Length > 20 || (s[0] == '0' && s.Length > 1))
        {
            return false;
        }

        for (int i = 0; i < s.Length; i++)
        {
            uint d = (uint)(s[i] - '0');
            if (d > 9 || (i == 19 && value > (ulong.MaxValue - d) / 10))
            {
                return false;
            }

            value = value * 10 + d;
        }

        return true;
    }

    static ulong Unsigned(ref Utf8JsonReader reader, ulong max)
    {
        if (reader.TokenType != JsonTokenType.Number || !Digits(reader.ValueSpan, out ulong v) || v > max)
        {
            throw new BenchException("json_unsigned");
        }

        return v;
    }

    static long Signed(ref Utf8JsonReader reader)
    {
        ReadOnlySpan<byte> s = reader.ValueSpan;
        bool negative = !s.IsEmpty && s[0] == '-';
        if (reader.TokenType != JsonTokenType.Number || !Digits(negative ? s[1..] : s, out ulong v) || v > 1_000_000)
        {
            throw new BenchException("json_value_range");
        }

        return negative ? -(long)v : (long)v;
    }
}
