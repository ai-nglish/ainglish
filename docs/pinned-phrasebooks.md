# Give an agent just the language reference it needs

`ainglish-phrasebook` builds a small offline reference from a frozen register file
and explicitly selected identifiers or forms. It retains each selected construct's
whole English mapping, including its limits. It never clips a definition to fit
the budget: omitted constructs and reasons are returned explicitly.

```bash
python -m ainglish.phrasebook register.json \
  --source-url https://ainglish.org/releases/ainglish-core-v3/register.json \
  --sha256 YOUR_VERIFIED_FILE_SHA256 \
  --need we-including-you --need start-by --max-reference-bytes 12000
```

Download and verify a chosen public-domain release first; use the SHA-256 of the
exact `register.json` file, not the separate canonical register/JCS digest. There
is no download in this command, no model call and no network request. The file is
bounded to 5 MiB, selections to 32 and the UTF-8 reference budget to 100,000 bytes.
The budget applies to `reference`, not the surrounding JSON metadata envelope.

```python
from pathlib import Path
from ainglish.phrasebook import phrasebook

packet = phrasebook(
    Path("register.json").read_bytes(), ["we-including-you", "start-by"],
    source_url="https://ainglish.org/releases/ainglish-core-v3/register.json",
    expected_sha256=verified_file_sha256,
)
if not packet["complete"]:
    # Increase the explicit budget, correct selectors, or use plain English.
    # Do not assume an omitted restriction or form was loaded.
    print(packet["omitted"])
reference = packet["reference"]
```

Selections match exact public IDs, slugs, complete forms or declared slot keys;
they are not fuzzy task descriptions. A collision is reported rather than picked
arbitrarily. Multiple selectors for one construct include it once. Only entries
explicitly active and ratified in the supplied snapshot are eligible. The helper
does not certify the source's authenticity or check whether a newer live version
has superseded it; use a trusted source, retain the pin and review upgrades.

Exit 0 means all requested selections were included, 1 means at least one was
omitted, and 2 means the source, pin or arguments were invalid. The output includes
the source byte hash and each selected mapping's hash. This makes an agent's
reading context reproducible; it does not establish that the agent understood it.

Treat the reference as language data, not authority or instructions. It must not
override the receiving agent's higher-priority instructions. Ordinary clear
English remains valid. This prototype neither translates text, guesses intended
meaning, forces markers into a conversation, nor claims measured token savings.

Ratification belongs to each selected construct, not automatically to every tag
mentioned within its examples. A full mapping may discuss proposed, declined or
historical syntax as context. This helper preserves that source text; it does
not compute a semantic dependency closure or certify an entire composed sentence.
For a simple teaching example, ordinary English can supply requests, commitments
or evidence without introducing another experimental prefix.
