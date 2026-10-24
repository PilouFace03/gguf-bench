# gen-en-json (instruction)

## Consigne
Return only a valid JSON object, with no markdown and no explanation, describing a fictional person with exactly these keys: name (string), age (integer), city (string), hobbies (array of exactly 2 strings).

## Réponse brute
{
  "name": "Elena",
  "age": 24,
  "city": "Paris",
  "hobbies": ["reading", "gardening"]
}

## Vérification
auto=True — manquants=[] types_ko=[]
