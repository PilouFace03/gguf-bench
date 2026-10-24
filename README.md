# Bench GGUF — vitesse + qualité sur llama.cpp

Banc d'essai pour une liste de fichiers `.gguf` : un seul fichier Python
(`bench_gguf.py`) qui lance `llama-server`, mesure le débit, évalue la qualité,
écrit les résultats, arrête le serveur et passe au modèle suivant. Un tableau de
bord web permet de suivre l'exécution en direct.

## État de la machine

| | |
|---|---|
| CPU | Intel i5-8500, 6 cœurs / 6 threads (AVX2) |
| RAM | 8 Go, **sans swap** → garde-fou mémoire dans le script |
| GPU | aucun (bench 100 % CPU) |
| llama.cpp | `v0.7.0` compilé sur place (`-DGGML_NATIVE=ON`), dans `/root/llama.cpp` |

## Utilisation

```bash
# lister les modèles vus par le script (archi, quant, params, ctx natif)
python3 /root/bench/bench_gguf.py --list

# série complète + tableau de bord web sur le port 8080
python3 /root/bench/bench_gguf.py --models /root/models --out /root/bench/results \
    --ctx 4096 --threads 6 --serve 8080

# revoir les résultats d'une précédente session
python3 /root/bench/bench_gguf.py --serve-only 8080
```

Le tableau de bord est ensuite accessible depuis le réseau à
`http://<ip-du-lxc>:8080/` (l'IP est affichée au démarrage, ici 192.168.0.200).

Variantes utiles :

```bash
python3 bench_gguf.py --mode speed      # débit uniquement
python3 bench_gguf.py --mode eval       # qualité uniquement
python3 bench_gguf.py --force           # refaire même si déjà mesuré
python3 bench_gguf.py --limit 1         # tester le premier modèle
python3 bench_gguf.py --rotations 2     # QCM 2× plus rapide, biais moins bien corrigé
python3 bench_gguf.py --stop 8080       # arrêter le tableau de bord du port 8080
```

Ajouter un modèle = déposer le `.gguf` dans `/root/models` (ou passer un chemin de
fichier à `--models`). Rien d'autre à modifier. Les fichiers placés dans un
sous-dossier (ex. `_hors_test/`) sont ignorés.

## Arrêter le tableau de bord

Commande intégrée, sans dépendance (`lsof`/`fuser` inutiles) :

```bash
python3 /root/bench/bench_gguf.py --stop 8080
```

Elle retrouve le PID qui écoute sur le port via `/proc`, envoie `SIGTERM`, attend
5 s, puis `SIGKILL` si le processus résiste. Code retour `0` = port libéré,
`1` = rien à arrêter ou port toujours occupé.

Équivalents si tu préfères le faire à la main :

```bash
kill $(lsof -ti :8080)          # si lsof est installé
ss -ltnp 'sport = :8080'        # retrouver le PID, puis kill <pid>
kill 20167                      # PID affiché au démarrage du tableau de bord
```

⚠️ **Ne pas utiliser `pkill -f bench_gguf.py`** : le motif correspond aussi à la
ligne de commande du shell qui l'exécute, qui se tue avant d'avoir tué le serveur.
Si tu y tiens, casse le motif : `pkill -f "[b]ench_gguf.py"`.

Deux cas de figure :

- lancé en `--serve-only PORT` : le processus ne fait que servir les résultats,
  `--stop` le termine sans rien perdre ;
- lancé avec `--serve PORT` pendant une campagne : c'est **le benchmark lui-même**
  qui écoute. `--stop` ou `kill` arrête donc aussi la mesure en cours. Préfère
  `Ctrl-C` (les résultats déjà obtenus sont écrits au fil de l'eau), ou attends la
  fin — le tableau de bord reste alors actif jusqu'à `Ctrl-C`.

Le serveur écoute sur `0.0.0.0` : il est joignable depuis tout le réseau local, pas
seulement depuis le LXC. Sur un LAN de confiance c'est voulu ; sinon filtre le port
8080 au niveau du pare-feu Proxmox.

## Contexte (ctx) : 4096 pour tout le monde

Le script alloue **4096 tokens de contexte**, jamais la valeur native (262 144 pour
Qwen3.5, 1 048 576 pour Spark). Le contexte natif ne sert qu'à l'affichage `--list` :
c'est un maximum que le modèle *peut* gérer, pas une allocation.

Pourquoi 4096 suffit : les prompts du bench font moins de 400 tokens, et les
réponses génératives sont plafonnées à 500 tokens. Réduire le contexte diminue la
RAM du cache KV et accélère légèrement le préfill, sans changer les mesures.

Un garde-fou réduit automatiquement à 2048 si le modèle + le cache KV + la marge
dépassent 95 % de la RAM disponible (ce n'est plus le cas avec la sélection actuelle).

## Modèles de cette campagne

| Fichier | Repo | Taille | Note |
|---|---|---|---|
| Qwen3.5-0.8B-Q4_K_M | unsloth/Qwen3.5-0.8B-GGUF | 0,53 Go | |
| Qwen3.5-2B-Q4_K_M | unsloth/Qwen3.5-2B-GGUF | 1,28 Go | le repo `unsloth/Qwen3.5-2B` ne contient pas de GGUF |
| Qwen3.5-4B-Q4_K_M | unsloth/Qwen3.5-4B-GGUF | 2,74 Go | |
| Spark-X2.6-4B-Q4_K_M | XHToken/Spark-X2.6-4B-GGUF | 3,54 Go | archi `spark2_5` |

Écartés (déplacés dans `/root/models/_hors_test/`, récupérables d'un `mv`) :

| Fichier | Taille | Raison |
|---|---|---|
| Qwen3.5-9B-Q4_K_M | 5,68 Go | trop lent / trop juste en RAM pour un usage local rapide |
| Nemotron-3.5-Bolt-E4B-Q4_K_M | 6,18 Go | MoE 7,52 B (4 B actifs), ~6,5 Gio nécessaires pour 7,2 dispo |

Avec les 4 modèles retenus, la RAM utilisée plafonne à ~4,1 Gio (le plus gros fait
3,54 Go) : aucun risque de pagination.

## Ce que mesure le script

**Vitesse** (3 répétitions, médiane, `temperature 0`) :

- `préfill` (`pp`) : tokens/s d'ingestion du prompt (~400 tokens) ;
- `génération` (`tg`) : tokens/s sur 128 tokens (`ignore_eos`) ;
- `TTFT` + débit en streaming : première réponse et vitesse perçue.

**Qualité** :

- **40 QCM** FR/EN (maths, logique, code, sciences, français, culture).
  Chaque question est posée **4 fois avec les choix décalés** : indispensable, sinon
  un petit modèle qui répond toujours « A » obtient un score artificiel.
  - `QCM` (soft) : probabilité moyenne donnée à la bonne réponse — métrique principale ;
  - `juste` : le choix le plus probable est-il le bon (non-réponse = faux) ;
  - `biais pos.` : probabilité moyenne de la première proposition (0,25 = neutre).
- **6 tâches génératives** (raisonnement FR, résumé contraint, code testé par
  assertions, JSON strict, traduction, format à puces), auto-notées quand c'est
  vérifiable, réponse brute sauvegardée pour relecture.

## Pendant l'exécution : ce que tu vois

Console et `results/run.log` :

```
[15:05:57] [1/4] Qwen3.5-0.8B-Q4_K_M.gguf
[15:05:57]     qwen35 · Q4_K_M · 0.75 B · 0.53 Go · ctx 4096
[15:05:59]     serveur prêt en 2.0 s (n_ctx=4096)
[15:06:22]     vitesse : préfill 272.0 tok/s · génération 35.8 tok/s · TTFT 151 ms
[15:06:22]     QCM : 40 questions × 4 rotations
[15:06:29]     QCM 16/160 (10 %)
...
[15:07:26]     génératif : 6 tâches
[15:07:40]     qualité : QCM 50% (juste 70%) · génératif 83%
[15:07:40]     terminé en 103 s
```

Tableau de bord `http://<ip>:8080/` (rafraîchi toutes les 3 s, sans recharger la page) :

- bandeau d'état : modèle en cours, phase (`chargement` → `vitesse` → `QCM` →
  `gén.` → `terminé`) et avancement (`QCM · 34/160`) ;
- tableau comparatif avec barres colorées (vert ≥ 70 %, orange ≥ 40 %, rouge en dessous) ;
- bloc dépliable par modèle : score par catégorie de QCM, détail des 6 tâches
  génératives, biais de position, liens vers les réponses brutes ;
- fin de page : les 80 dernières lignes du journal ;
- `/results.csv` téléchargeable, `/raw/...` pour les sorties brutes.

## Sorties

```
results/
  results.jsonl   # une ligne par modèle (complet, avec détail QCM/gén.)
  results.csv     # tableau plat, prêt pour LibreOffice/pandas
  summary.md      # classement lisible
  state.json      # état temps réel (utilisé par le tableau de bord)
  run.log         # journal horodaté de l'exécution
  raw/<modèle>/   # réponses brutes + mcq.json (probs par question)
  logs/<modèle>.log  # sortie de llama-server (diagnostic en cas d'échec)
```

## Limites connues

- Les tok/s dépendent du contexte, de la quantization et de la charge de l'hôte
  (LXC sur Proxmox) : 3 répétitions + médiane limitent le bruit, pas la dérive.
- 40 questions ≈ ±8 % d'incertitude : suffisant pour classer, pas pour trancher
  deux modèles à 3 points d'écart.
- Les tâches génératives sont auto-notées par des règles simples (premier nombre,
  JSON, assertions) : une réponse juste formulée autrement peut être comptée fausse.
- ⚠️ Le test de code **exécute du Python produit par le modèle** (sous-processus,
  20 s max, sans bac à sable). À ne lancer que sur une machine de bench.
