using System.Runtime.CompilerServices;
using System.Runtime.Intrinsics;
using System.Runtime.Intrinsics.X86;
using System.Text.Unicode;

namespace TcpBench;

// UTF-8 validation with the lookup algorithm of Keiser and Lemire ("Validating UTF-8 in less than one
// instruction per byte", 2021), as in simdjson: three 16-entry nibble tables classify each byte pair,
// and the 2nd/3rd/4th-byte rule is checked with saturating subtraction. 64-byte ASCII blocks are
// skipped. Same result as Utf8.IsValid (the selftest compares them); falls back to it without AVX2.
static unsafe class Utf8Check
{
    const byte TooShort = 1, TooLong = 2, Overlong3 = 4, TooLarge = 8, Surrogate = 16, Overlong2 = 32, TooLarge1000 = 64,
        Overlong4 = 64, TwoConts = 128, Carry = TooShort | TooLong | TwoConts;

    public static bool IsValid(ReadOnlySpan<byte> input)
    {
        if (!Avx2.IsSupported)
        {
            return Utf8.IsValid(input);
        }

        fixed (byte* start = input)
        {
            return Check(start, input.Length);
        }
    }

    static bool Check(byte* p, long length)
    {
        Vector256<byte> firstHigh = Vector256.Create(
            TooLong, TooLong, TooLong, TooLong, TooLong, TooLong, TooLong, TooLong,
            TwoConts, TwoConts, TwoConts, TwoConts, TooShort | Overlong2, TooShort, TooShort | Overlong3 | Surrogate,
            TooShort | TooLarge | TooLarge1000 | Overlong4,
            TooLong, TooLong, TooLong, TooLong, TooLong, TooLong, TooLong, TooLong,
            TwoConts, TwoConts, TwoConts, TwoConts, TooShort | Overlong2, TooShort, TooShort | Overlong3 | Surrogate,
            TooShort | TooLarge | TooLarge1000 | Overlong4);
        const byte L = Carry | TooLarge | TooLarge1000;
        Vector256<byte> firstLow = Vector256.Create(
            Carry | Overlong3 | Overlong2 | Overlong4, Carry | Overlong2, Carry, Carry, Carry | TooLarge, L, L, L,
            L, L, L, L, L, L | Surrogate, L, L,
            Carry | Overlong3 | Overlong2 | Overlong4, Carry | Overlong2, Carry, Carry, Carry | TooLarge, L, L, L,
            L, L, L, L, L, L | Surrogate, L, L);
        const byte C8 = TooLong | Overlong2 | TwoConts | Overlong3 | TooLarge1000 | Overlong4;
        const byte C9 = TooLong | Overlong2 | TwoConts | Overlong3 | TooLarge;
        const byte CAB = TooLong | Overlong2 | TwoConts | Surrogate | TooLarge;
        Vector256<byte> secondHigh = Vector256.Create(
            TooShort, TooShort, TooShort, TooShort, TooShort, TooShort, TooShort, TooShort,
            C8, C9, CAB, CAB, TooShort, TooShort, TooShort, TooShort,
            TooShort, TooShort, TooShort, TooShort, TooShort, TooShort, TooShort, TooShort,
            C8, C9, CAB, CAB, TooShort, TooShort, TooShort, TooShort);
        // A block ends mid-sequence if its last byte is a lead, or the 2nd/3rd-last lead 3/4-byte sequences.
        Vector256<byte> incompleteLimit = Vector256.Create(
            (byte)255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255,
            255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 0xEF, 0xDF, 0xBF);

        Vector256<byte> previous = Vector256<byte>.Zero, error = Vector256<byte>.Zero, incomplete = Vector256<byte>.Zero;
        long i = 0;
        for (; i + 64 <= length; i += 64)
        {
            Vector256<byte> a = Avx.LoadVector256(p + i), b = Avx.LoadVector256(p + i + 32);
            if (Avx2.MoveMask(a | b) == 0)
            {
                error |= incomplete;
                incomplete = Vector256<byte>.Zero;
                previous = b;
                continue;
            }

            error |= Block(a, previous, firstHigh, firstLow, secondHigh);
            error |= Block(b, a, firstHigh, firstLow, secondHigh);
            incomplete = Avx2.SubtractSaturate(b, incompleteLimit);
            previous = b;
        }

        byte* tail = stackalloc byte[32];
        for (; i < length; i += 32)
        {
            Vector256<byte> a;
            if (length - i >= 32)
            {
                a = Avx.LoadVector256(p + i);
            }
            else
            {
                Unsafe.InitBlockUnaligned(tail, 0, 32); // zero padding is ASCII
                Buffer.MemoryCopy(p + i, tail, 32, length - i);
                a = Avx.LoadVector256(tail);
            }

            error |= Block(a, previous, firstHigh, firstLow, secondHigh);
            incomplete = Avx2.SubtractSaturate(a, incompleteLimit);
            previous = a;
        }

        error |= incomplete;
        return Avx.TestZ(error, error);
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static Vector256<byte> Block(Vector256<byte> input, Vector256<byte> previous, Vector256<byte> firstHigh,
        Vector256<byte> firstLow, Vector256<byte> secondHigh)
    {
        // Bytes shifted by 1, 2 and 3 positions across the block boundary.
        Vector256<byte> carried = Avx2.Permute2x128(previous, input, 0x21);
        Vector256<byte> prev1 = Avx2.AlignRight(input, carried, 15);
        Vector256<byte> prev2 = Avx2.AlignRight(input, carried, 14);
        Vector256<byte> prev3 = Avx2.AlignRight(input, carried, 13);
        Vector256<byte> nibble = Vector256.Create((byte)0x0F);
        Vector256<byte> special =
            Avx2.Shuffle(firstHigh, Avx2.ShiftRightLogical(prev1.AsUInt16(), 4).AsByte() & nibble) &
            Avx2.Shuffle(firstLow, prev1 & nibble) &
            Avx2.Shuffle(secondHigh, Avx2.ShiftRightLogical(input.AsUInt16(), 4).AsByte() & nibble);
        Vector256<byte> mustContinue = Avx2.SubtractSaturate(prev2, Vector256.Create((byte)(0xE0 - 0x80))) |
            Avx2.SubtractSaturate(prev3, Vector256.Create((byte)(0xF0 - 0x80)));
        return (mustContinue & Vector256.Create((byte)0x80)) ^ special;
    }
}
