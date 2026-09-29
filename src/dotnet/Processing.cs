using System.Buffers;
using System.Buffers.Binary;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace TcpBench;
// Default disables the budget for selftests and processing-only controls.
// Network processing checks every 1024 rows; an individual JSON token or array resize is not preemptible.
readonly struct ProcessingBudget(long deadline, CancellationToken stop)
{
    public void Check()
    {
        stop.ThrowIfCancellationRequested();
        if (deadline != 0 && Stopwatch.GetTimestamp() >= deadline)
        {
            throw new TimeoutException("processing_deadline");
        }
    }

    public async ValueTask Pause(int milliseconds)
    {
        long finish = Stopwatch.GetTimestamp() + (long)milliseconds * Stopwatch.Frequency / 1000;
        while (true)
        {
            Check();
            long now = Stopwatch.GetTimestamp();
            if (now >= finish)
            {
                return;
            }
            long until = deadline == 0 ? finish : Math.Min(finish, deadline);
            double delayMs = Math.Max(1, Math.Ceiling((double)(until - now) * 1000 / Stopwatch.Frequency));
            await Task.Delay(TimeSpan.FromMilliseconds(delayMs), stop);
        }
    }
}

static class Digest
{
    public const ulong Offset = 14695981039346656037UL;
    public static ulong Byte(ulong h, byte b) => unchecked((h ^ b) * 1099511628211UL);
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
    public Row[] Rows = [];
    public byte[] Text = [];
    public int Count, TextLength;
    public ulong StoredDigest;
    public long CanonicalBytes => 38L * Count + TextLength;
    public long CapacityBytes => 48L * Rows.Length + Text.Length;

    public void Reset()
    {
        Count = 0;
        TextLength = 0;
    }

    public void Add(Row row, ReadOnlySpan<byte> text, long limit)
    {
        if (CanonicalBytes + 38 + text.Length > limit)
        {
            throw new InvalidDataException("retention_capacity");
        }

        if (Count == Rows.Length)
        {
            Array.Resize(ref Rows, Math.Max(1, Math.Min(checked((int)Math.Min(limit / 38, int.MaxValue)), Math.Max(16, Rows.Length * 2))));
        }

        if (TextLength + text.Length > Text.Length)
        {
            Array.Resize(ref Text, Math.Max(TextLength + text.Length, (int)Math.Min(limit, Math.Max(256L, (long)Text.Length * 2))));
        }

        row.Offset = TextLength;
        row.Length = text.Length;
        text.CopyTo(Text.AsSpan(TextLength));
        TextLength += text.Length;
        Rows[Count++] = row;
    }

    public ulong Visit(Span<ulong> counts, Span<long> sums, ProcessingBudget budget = default)
    {
        budget.Check();
        ulong hash = Digest.Offset;
        for (int i = 0; i < Count; i++)
        {
            if ((i & 1023) == 0)
            {
                budget.Check();
            }

            ref Row row = ref Rows[i];
            hash = Digest.Row(hash, row, Text.AsSpan(row.Offset, row.Length));
            if (!counts.IsEmpty)
            {
                int bucket = row.Kind * 4 + (int)(row.Flags & 3);
                counts[bucket]++;
                sums[bucket] += row.Value;
            }
        }

        budget.Check();
        return hash;
    }

    public void Verify(ProcessingBudget budget = default)
    {
        if (Visit([], [], budget) != StoredDigest)
        {
            throw new InvalidDataException("retained_integrity");
        }
    }
}

sealed class Summary
{
    public ulong Batches;
    public ulong Bytes;
    public ulong Records;
    public ulong Hash = Digest.Offset;
    public readonly ulong[] Counts = new ulong[64];
    public readonly long[] Sums = new long[64];
    public void Reset()
    {
        Batches = Bytes = Records = 0;
        Hash = Digest.Offset;
        Array.Clear(Counts);
        Array.Clear(Sums);
    }

    public void Add(ulong sequence, int bytes, ulong records, ulong digest, ReadOnlySpan<ulong> counts, ReadOnlySpan<long> sums)
    {
        if (records > 1_000_000_000UL - Records)
        {
            throw new InvalidDataException("epoch_record_limit");
        }

        checked
        {
            Batches++;
            Bytes += (ulong)bytes;
            Records += records;
        }

        Hash = Digest.Number(Digest.Number(Digest.Number(Hash, sequence, 8), records, 8), digest, 8);
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

    public void Verify(ReadOnlySpan<byte> body)
    {
        Span<byte> expected = stackalloc byte[1056];
        Write(expected);
        if (!body.SequenceEqual(expected))
        {
            throw new InvalidDataException("end_summary_mismatch");
        }
    }
}

sealed class Processor
{
    readonly string mode;
    readonly int retainBatches;
    readonly long retainBytes;
    readonly Queue<Batch> retained = new();
    Batch? spare;
    public readonly Summary Epoch = new();
    readonly ulong[] counts = new ulong[64];
    readonly long[] sums = new long[64];
    public long RetainedBytes { get; private set; }
    public long PeakRetainedBytes { get; private set; }
    public long PeakOwnedCapacityBytes { get; private set; }
    public long LastDecodeTicks { get; private set; }
    public long LastVisitTicks { get; private set; }

    public long OwnedCapacityBytes
    {
        get
        {
            long bytes = spare?.CapacityBytes ?? 0;
            foreach (Batch batch in retained)
            {
                bytes += batch.CapacityBytes;
            }

            return bytes;
        }
    }

    public Processor(string mode, int retainBatches, long retainBytes)
    {
        this.mode = mode;
        this.retainBatches = retainBatches;
        this.retainBytes = retainBytes;
    }

    public (ulong Records, ulong Hash) Process(ReadOnlySpan<byte> payload, ulong sequence, bool sampleStages = false, ProcessingBudget budget = default)
    {
        budget.Check();
        long decodeStart = sampleStages ? Stopwatch.GetTimestamp() : 0;
        Array.Clear(counts);
        Array.Clear(sums);
        // Scratch is never a committed batch, even when reuse recycles a previously evicted buffer.
        Batch? staged = mode switch
        {
            "retain-reuse" => spare ?? new Batch(),
            "retain-allocate" => new Batch(),
            _ => null
        };
        staged?.Reset();
        try
        {
            ulong records, hash;
            if (mode == "transport")
            {
                records = 0;
                hash = (ulong)payload.Length;
            }
            else
            {
                (records, hash) = Parse(payload, staged, budget);
            }

            if (sampleStages)
            {
                LastDecodeTicks = Stopwatch.GetTimestamp() - decodeStart;
                LastVisitTicks = 0;
            }

            if (records > 1_000_000_000UL - Epoch.Records)
            {
                throw new InvalidDataException("epoch_record_limit");
            }

            ObserveOwnedCapacity(staged);
            if (staged != null)
            {
                long visitStart = sampleStages ? Stopwatch.GetTimestamp() : 0;
                Array.Clear(counts);
                Array.Clear(sums);
                hash = staged.Visit(counts, sums, budget);
                staged.StoredDigest = hash;
                int evictions = 0;
                long remainingBytes = RetainedBytes;
                // Validate every planned eviction before mutating committed retention or epoch state.
                foreach (Batch old in retained)
                {
                    if (retained.Count - evictions < retainBatches && remainingBytes + staged.CanonicalBytes <= retainBytes)
                    {
                        break;
                    }

                    old.Verify(budget);
                    remainingBytes -= old.CanonicalBytes;
                    evictions++;
                }

                budget.Check();
                // Commit is deliberately uninterrupted: cancellation must not leave half an eviction applied.
                spare = null;
                for (int i = 0; i < evictions; i++)
                {
                    Batch old = retained.Dequeue();
                    RetainedBytes -= old.CanonicalBytes;
                    if (mode == "retain-reuse")
                    {
                        spare = old;
                    }
                }

                retained.Enqueue(staged);
                RetainedBytes += staged.CanonicalBytes;
                staged = null;
                PeakRetainedBytes = Math.Max(PeakRetainedBytes, RetainedBytes);
                if (sampleStages)
                {
                    LastVisitTicks = Stopwatch.GetTimestamp() - visitStart;
                }
            }
            else
            {
                budget.Check();
            }

            Epoch.Add(sequence, payload.Length, records, hash, counts, sums);
            return (records, hash);
        }
        finally
        {
            // A rejected/cancelled parse can grow scratch before failing. Include it before it is released.
            ObserveOwnedCapacity(staged);
        }
    }

    void ObserveOwnedCapacity(Batch? staged)
    {
        long bytes = OwnedCapacityBytes;
        if (staged != null && !ReferenceEquals(staged, spare))
        {
            bytes += staged.CapacityBytes;
        }

        PeakOwnedCapacityBytes = Math.Max(PeakOwnedCapacityBytes, bytes);
    }

    public void VerifyRetained(ProcessingBudget budget = default)
    {
        budget.Check();
        foreach (Batch batch in retained)
        {
            batch.Verify(budget);
        }

        budget.Check();
    }

    internal void VerifyCategories(ReadOnlySpan<ulong> expectedCounts, ReadOnlySpan<long> expectedSums)
    {
        if (!counts.AsSpan().SequenceEqual(expectedCounts) || !sums.AsSpan().SequenceEqual(expectedSums))
        {
            throw new InvalidDataException("process_oracle");
        }
    }

    (ulong, ulong) Parse(ReadOnlySpan<byte> input, Batch? batch, ProcessingBudget budget)
    {
        var reader = new Utf8JsonReader(input, new JsonReaderOptions
        {
            MaxDepth = 4,
            CommentHandling = JsonCommentHandling.Disallow,
            AllowTrailingCommas = false
        });
        if (!reader.Read() || reader.TokenType != JsonTokenType.StartArray)
        {
            throw new InvalidDataException("json_root");
        }

        Span<byte> message = stackalloc byte[4096];
        Span<byte> name = stackalloc byte[64];
        Span<byte> kind = stackalloc byte[6];
        ulong hash = Digest.Offset, records = 0;
        while (reader.Read() && reader.TokenType != JsonTokenType.EndArray)
        {
            if ((records & 1023) == 0)
            {
                budget.Check();
            }

            if (reader.TokenType != JsonTokenType.StartObject)
            {
                throw new InvalidDataException("json_record");
            }

            Row row = default;
            int seen = 0, messageLength = 0;
            while (reader.Read() && reader.TokenType != JsonTokenType.EndObject)
            {
                if (reader.TokenType != JsonTokenType.PropertyName)
                {
                    throw new InvalidDataException("json_property");
                }

                int nameLength = Decode(ref reader, name);
                ReadOnlySpan<byte> field = name[..nameLength];
                int bit = field.SequenceEqual("id"u8) ? 1
                    : field.SequenceEqual("timestamp_ns"u8) ? 2
                    : field.SequenceEqual("source"u8) ? 4
                    : field.SequenceEqual("kind"u8) ? 8
                    : field.SequenceEqual("value_milli"u8) ? 16
                    : field.SequenceEqual("flags"u8) ? 32
                    : field.SequenceEqual("message"u8) ? 64
                    : 0;
                if (bit == 0 || (seen & bit) != 0 || !reader.Read())
                {
                    throw new InvalidDataException("json_fields");
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
                        if (reader.TokenType != JsonTokenType.String || Decode(ref reader, kind) != 6 ||
                            !kind[..4].SequenceEqual("kind"u8) || kind[4] < '0' || kind[4] > '1' || kind[5] < '0' || kind[5] > '9')
                        {
                            throw new InvalidDataException("json_kind");
                        }

                        row.Kind = (byte)((kind[4] - '0') * 10 + kind[5] - '0');
                        if (row.Kind > 15)
                        {
                            throw new InvalidDataException("json_kind");
                        }

                        break;
                    case 16:
                        IntegerLexeme(ref reader, true);
                        if (!reader.TryGetInt64(out row.Value) || row.Value < -1_000_000 || row.Value > 1_000_000)
                        {
                            throw new InvalidDataException("json_value_range");
                        }

                        break;
                    case 32:
                        row.Flags = (uint)Unsigned(ref reader, uint.MaxValue);
                        break;
                    case 64:
                        if (reader.TokenType != JsonTokenType.String)
                        {
                            throw new InvalidDataException("json_message");
                        }

                        messageLength = Decode(ref reader, message);
                        break;
                }
            }

            if (seen != 127 || reader.TokenType != JsonTokenType.EndObject)
            {
                throw new InvalidDataException("json_missing_field");
            }

            if (++records > 1_000_000_000)
            {
                throw new InvalidDataException("epoch_record_limit");
            }

            if (batch != null)
            {
                batch.Add(row, message[..messageLength], retainBytes);
            }
            else
            {
                hash = Digest.Row(hash, row, message[..messageLength]);
                int bucket = row.Kind * 4 + (int)(row.Flags & 3);
                counts[bucket]++;
                sums[bucket] += row.Value;
            }
        }

        if (reader.TokenType != JsonTokenType.EndArray || reader.Read())
        {
            throw new InvalidDataException("json_trailing");
        }

        return (records, hash);
    }

    static void IntegerLexeme(ref Utf8JsonReader reader, bool signed)
    {
        if (reader.TokenType != JsonTokenType.Number)
        {
            throw new InvalidDataException("json_integer");
        }

        ReadOnlySpan<byte> token = reader.ValueSpan;
        int i = signed && token[0] == '-' ? 1 : 0;
        if (i == token.Length)
        {
            throw new InvalidDataException("json_integer");
        }

        for (; i < token.Length; i++)
        {
            if (token[i] < '0' || token[i] > '9')
            {
                throw new InvalidDataException("json_integer");
            }
        }
    }

    static ulong Unsigned(ref Utf8JsonReader reader, ulong max)
    {
        IntegerLexeme(ref reader, false);
        if (!reader.TryGetUInt64(out ulong value) || value > max)
        {
            throw new InvalidDataException("json_unsigned_range");
        }

        return value;
    }

    static int Hex(ReadOnlySpan<byte> value)
    {
        int n = 0;
        foreach (byte b in value)
        {
            int digit = b switch
            {
                >= (byte)'0' and <= (byte)'9' => b - '0',
                >= (byte)'a' and <= (byte)'f' => b - 'a' + 10,
                >= (byte)'A' and <= (byte)'F' => b - 'A' + 10,
                _ => -1
            };
            if (digit < 0)
            {
                throw new InvalidDataException("json_escape");
            }

            n = (n << 4) | digit;
        }

        return n;
    }

    static int Decode(ref Utf8JsonReader reader, scoped Span<byte> target)
    {
        ReadOnlySpan<byte> raw = reader.ValueSpan;
        // Utf8JsonReader exposes raw strings. Explicitly reject surrogate substitutions before unescaping.
        if (reader.ValueIsEscaped)
        {
            for (int i = 0; i < raw.Length; i++)
            {
                if (raw[i] != '\\')
                {
                    continue;
                }

                if (++i >= raw.Length)
                {
                    throw new InvalidDataException("json_escape");
                }

                if (raw[i] != 'u')
                {
                    continue;
                }

                if (i + 4 >= raw.Length)
                {
                    throw new InvalidDataException("json_escape");
                }

                int code = Hex(raw.Slice(i + 1, 4));
                i += 4;
                if (code is >= 0xDC00 and <= 0xDFFF)
                {
                    throw new InvalidDataException("json_surrogate");
                }

                if (code is >= 0xD800 and <= 0xDBFF)
                {
                    if (i + 6 >= raw.Length || raw[i + 1] != '\\' || raw[i + 2] != 'u')
                    {
                        throw new InvalidDataException("json_surrogate");
                    }

                    int low = Hex(raw.Slice(i + 3, 4));
                    if (low < 0xDC00 || low > 0xDFFF)
                    {
                        throw new InvalidDataException("json_surrogate");
                    }

                    i += 6;
                }
            }
        }

        int length;
        try
        {
            length = reader.CopyString(target);
        }
        catch (ArgumentException)
        {
            throw new InvalidDataException("json_string_length");
        }

        ReadOnlySpan<byte> decoded = target[..length];
        while (!decoded.IsEmpty)
        {
            if (Rune.DecodeFromUtf8(decoded, out _, out int consumed) != OperationStatus.Done)
            {
                throw new InvalidDataException("json_utf8");
            }

            decoded = decoded[consumed..];
        }

        return length;
    }
}
