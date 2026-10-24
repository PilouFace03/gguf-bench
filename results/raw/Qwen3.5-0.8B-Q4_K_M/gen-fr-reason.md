# gen-fr-reason (raisonnement)

## Consigne
Un fermier élève des poules et des lapins. Il compte 35 têtes et 94 pattes. Combien y a-t-il de lapins ? Donne le nombre puis une courte explication.

## Réponse brute
Il y a **15 lapins**.

**Explication :**
Le fermier compte 35 têtes et 94 pattes. Comme chaque poule a 2 pattes et chaque lapin a 4, on peut calculer le nombre de poules : $94 \div 2 = 47$ poules. Ensuite, on soustrait le nombre de poules du total des pattes : $94 - 47 = 47$. Ce nombre (47) correspond au nombre de têtes, donc il y a 47 poules. Le nombre de lapins est alors $94 - 47 = 47$.

## Vérification
auto=False — nombres trouvés=[15.0, 35.0, 94.0, 2.0, 4.0, 94.0] (attendu 12)
