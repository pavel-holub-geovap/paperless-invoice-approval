# AI extrakce faktury

## Kontrakt V3

Aktuální striktní schéma je `invoice-extraction.v3`. Obsahuje dodavatele, samostatné `supplier_address_raw`, `supplier_street`, `supplier_city`, `supplier_zip`, číslo faktury, variabilní symbol, data, platební údaje, VAT řádky a deklarované součty. VAT řádek rozlišuje `taxable_base`, `vat_amount`, `gross_amount` a může mít `adjustment_type=ROUNDING`. Pokud model prokazatelně zamění hrubou částku za DPH, normalizace zachová `vat_amount_extracted` a transparentně použije `gross_amount − taxable_base`; deklarované součty faktury se tím nemění. Každá skalární hodnota má `value` a krátký doslovný `source_text`; neznámá hodnota je explicitně `null`. Extra pole se odmítají. Historické v1/v2 výsledky zůstávají beze změny a při případném použití procházejí kompatibilním převodem.

Po přijetí JSON výstupu hranice konzervativně převede pouze jednoznačné lokalizované numerické řetězce (`21%`, desetinnou čárku, mezery tisíců a běžný měnový suffix) na Decimal; libovolný text nebo nejednoznačný formát dál odmítne striktní Pydantic validace. Následná deterministická vrstva rozloží český účet a normalizuje české PSČ. Adresní fallback pracuje výhradně nad již izolovanou hodnotou adresy dodavatele a vyžaduje právě jedno PSČ s neprázdnou ulicí i městem; nikdy nehledá „první PSČ“ v celém OCR. Označená VAT tabulka a sumář se zpracují samostatným přesným parserem: z dvojice vytištěný základ/hrubá částka vznikne DPH jako rozdíl, rounding zůstane samostatným řádkem a explicitně vytištěné součty se převezmou ze sumáře. Raw model response a `source_text` zůstávají append-only. Deklarované součty faktury se obecným matematickým přepočtem nepřepisují.

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
