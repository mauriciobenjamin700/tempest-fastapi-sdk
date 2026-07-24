# Checklist de validação manual (camada 3)

Preenchido por item que precisa de modelo/GPU real (ver tabela em
`validation-strategy.md`). Rodar localmente; **colar o resultado no corpo do
PR**. Se sem hardware/modelo, marcar explicitamente "não verificado" — nunca
deixar silencioso.

Detectar hardware antes:

```python
from tempest_fastapi_sdk.genai import probe_hardware
print(probe_hardware())  # CPU/RAM/CUDA/MPS
```

---

## #2 — tool calling (instruct real)

- [ ] Modelo: `Qwen/Qwen2.5-7B-Instruct` (ou menor conforme VRAM).
- [ ] 3 prompts do checklist escolhem a tool certa (3/3).
- Comando / saída:

```
(colar)
```

## #3 — structured output

- [ ] Schema simples preenchido com campos plausíveis e válidos.
- Comando / saída:

```
(colar)
```

## #10 — VLM

- [ ] `Qwen/Qwen2-VL-2B-Instruct` (ou LLaVA) descreve imagem de teste.
- Comando / saída:

```
(colar)
```

## #4 — reranker (qualidade)

- [ ] Rerank melhora a ordem vs denso em consulta ambígua do checklist.
- Comando / saída:

```
(colar)
```

## #12 — observabilidade

- [ ] Latência real plausível; tokens/s reportado.
- Comando / saída:

```
(colar)
```

## #7 — vision router

- [ ] Modelo ONNX real classifica/detecta via endpoint.
- Comando / saída:

```
(colar)
```

## #14 — moderação (PT-BR)

- [ ] Amostras tóxicas/limpas PT-BR rotuladas razoavelmente.
- Comando / saída:

```
(colar)
```
