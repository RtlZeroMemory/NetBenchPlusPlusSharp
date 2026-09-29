# Invalid campaign attempt, kept as failing evidence

Started 2026-09-29 as a trimmed campaign (five phases, 3 repetitions) and stopped after 69 trials.

Every C# trial on a HARD corpus failed: the C# server/processing control rejected valid records with
`json_message_length`. Cause: the managed message scratch was exactly 4,096 bytes, and
`Utf8JsonReader.CopyString` rejects an *exact fit* when the string contains escapes. A valid message
with escapes that decodes to exactly 4,096 bytes (the HARD profile's maximum) was therefore rejected.
Confirmed by mutation: restoring the 4,096-byte scratch makes the new selftest case
(`\u0041` followed by 4,095 plain bytes) fail. The native parser checks
the decoded length and accepted the same records. The smoke corpora were too small to contain such a
message.

Fixed in `src/dotnet/Processing.cs` (6 × 4,096-byte scratch plus an explicit decoded-length check),
with boundary selftests in both implementations. These results were produced by the defective C#
binary and must not be used. The declared campaign was rerun in `results/rework-campaign-20260929`.
