# Résultats du benchmark

Machine : 6 threads, 7.2 Gio dispo, llama.cpp `v0.7.0`

## Classement (qualité puis vitesse)

| Modèle | Params | Quant | Taille | ctx | pp tok/s | tg tok/s | TTFT | QCM (soft) | QCM (juste) | biais pos. | Gén. |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3.5-4B-Q4_K_M.gguf | 4.21 B | Q4_K_M | 2.74 Go | 4096 | 54.2 | 9.2 | 685 ms | 79 % | 82 % | 0.23 | 100 % |
| Spark-X2.6-4B-Q4_K_M.gguf | 4.45 B | Q4_K_M | 3.54 Go | 4096 | 53.0 | 10.1 | 739 ms | 69 % | 68 % | 0.24 | 83 % |
| Qwen3.5-2B-Q4_K_M.gguf | 1.88 B | Q4_K_M | 1.28 Go | 4096 | 134.7 | 18.9 | 255 ms | 63 % | 75 % | 0.20 | 83 % |
| Qwen3.5-0.8B-Q4_K_M.gguf | 0.75 B | Q4_K_M | 0.53 Go | 4096 | 272.0 | 35.8 | 151 ms | 50 % | 70 % | 0.39 | 83 % |

## Rappel méthodo

- `pp` = préfill (tok/s d'ingestion du prompt), `tg` = génération (tok/s).
- 3 répétitions, médiane ; `temperature 0`, même `ctx` et même `-t` pour tous.
- QCM : 40 questions FR/EN, chaque question posée 4 fois avec les choix décalés
  (le biais de position est ainsi neutralisé) ; « soft » = probabilité moyenne
  accordée à la bonne réponse, « juste » = choix le plus probable.
- `biais pos.` = probabilité moyenne donnée à la première proposition (0,25 = neutre).
- Gén. : 6 tâches, auto-scoring ; les réponses brutes sont dans `raw/`.
