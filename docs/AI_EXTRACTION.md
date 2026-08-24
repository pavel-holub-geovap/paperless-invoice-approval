# AI extrakce faktury

## Kontrakt V1

`InvoiceExtractionV1` je verzované striktní schéma `invoice-extraction.v1`. Obsahuje dodavatele (název, IČO, DIČ, adresu), číslo faktury, variabilní symbol, tři data, měnu, účet/kód banky, IBAN, SWIFT/BIC, DPH řádky, základ, DPH, celkovou částku a popis. Každá skalární hodnota má `value` a krátký doslovný `source_text`; neznámá hodnota je explicitně `null`. Extra pole se odmítají.

Po přijetí strict výstupu deterministická vrstva rozloží český účet do `bank_account_raw`, `bank_account_prefix`, `bank_account_number`, `bank_code` a kompatibilního `bank_account`. Raw model response a `source_text` zůstávají append-only; normalizace tedy neopravuje důkaz, ale pracovní hodnotu. Stejný kombinovaný řetězec v obou LLM polích se bezpečně rozpozná bez odhadu.

## Tok

1. Worker načte živý dokument a OCR pouze přes Paperless REST API.
2. Vytvoří append-only `AIExtraction` a technický stav `AI_PENDING/AI_PROCESSING`.
3. Ollama dostane celé schema v system promptu a vrátí JSON mode výstup; JSON parser a striktní Pydantic kontrakt ho ověří. JSON mode se používá kvůli omezením Ollama grammar parseru pro Decimal/date uniony.
4. Deterministická služba vytvoří výsledky `code`, `severity`, `field`, `message`, `expected`, `actual`.
5. První výsledek se použije automaticky pouze tehdy, když je pracovní revize prázdná. Další výsledek je bezpečný kandidát a vyžaduje explicitní potvrzení `QUEUE_MANAGER`.
6. Faktura zůstává v obchodním stavu `QUEUE_REVIEW`; AI selhání ho nemění.

Historie uchovává raw odpověď, parsed JSON, model, schema/prompt verzi, validační snapshot, duration, chybu a informaci o aplikaci. Audit obsahuje queued, started, extracted/failed, validation finished, applied, re-extraction requested a manuální změny polí. Historie se nepřepisuje.

## Chyby a retry

Databázový job má idempotency key, lease a maximálně `AI_EXTRACTION_MAX_ATTEMPTS` pokusů. Stabilní kódy jsou `OLLAMA_UNAVAILABLE`, `OLLAMA_TIMEOUT`, `INVALID_JSON`, `SCHEMA_VALIDATION_FAILED`, `PAPERLESS_ERROR` a `EXTRACTION_FAILED`. Po mezilehlém selhání se běh vrátí do `AI_PENDING`; po posledním do `AI_FAILED`.

## Bezpečnost

- OCR se považuje za prompt-injection data, nikoli instrukce.
- Browser nikdy nevolá Ollamu ani nevidí Paperless token.
- LLM nesmí vytvářet XML, SQL, workflow stav, cost center nebo approvera.
- Originální PDF zůstává v Paperless; do Approval DB se neukládá.
- Re-extrakce bez potvrzení nepřepíše uživatelské opravy.

## Výchozí produkční model po opravné iteraci

Výchozí hodnota je `qwen3:8b`, CPU-only, `num_ctx=4096`, jedna inference a timeout 900 s. Historické extrakce a jejich metadata se nemění; každý nový běh ukládá přesný název použitého modelu. Pád kvůli paměti je viditelná chyba a nesmí spustit automatický fallback na 4B.
