# LLM hranice

Ollama je v Etapě D pouze nedeterministický převodník OCR na kandidátní strukturu `InvoiceExtractionV1`. Účetní pravidla, workflow, přidělení středisek a schvalovatelů, XML i SQL jsou mimo její pravomoc. Produkční kód používá `temperature=0`, `num_ctx=4096`, `num_gpu=0`, jediný paralelní požadavek a konfigurovatelný model (výchozí `qwen3:4b`).

OCR je nedůvěryhodný obsah. System prompt výslovně zakazuje provádět instrukce z dokumentu a user message jej uzavírá do `<invoice_ocr_data>`. Výstup musí projít JSON parserem a striktním Pydantic schématem; jinak se uloží technická chyba a obchodní stav faktury se nezmění.

Podrobnosti persistence, retry a re-extrakce jsou v `AI_EXTRACTION.md`.
