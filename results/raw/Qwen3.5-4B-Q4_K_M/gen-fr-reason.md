# gen-fr-reason (raisonnement)

## Consigne
Un fermier élève des poules et des lapins. Il compte 35 têtes et 94 pattes. Combien y a-t-il de lapins ? Donne le nombre puis une courte explication.

## Réponse brute
Il y a **25** lapins.

**Explication :**
Si l'on suppose que tous les animaux sont des poulets (2 pattes), 35 poulets donneraient 70 pattes. Il manque donc 24 pattes (94 - 70). Comme chaque lapin a 2 pattes de plus qu'un poulet, il faut ajouter 12 lapins pour avoir 24 pattes supplémentaires. Cela fait donc 12 + 23 = 35 poulets et 12 lapins.

*Correction de la logique ci-dessus pour être plus directe :*
1. Supposons 35 poulets : 35 × 2 = 70 pattes.
2. Il reste 94 - 70 = 24 pattes de trop.
3. Chaque lapin apporte 2 pattes de plus qu'un poulet.
4. Nombre de lapins = 24 / 2 = 12.
5. Vérification : 12 lapins (24 pattes) + 23 poulets (46 pattes) = 67 pattes ? Non, recalculons.

**Calcul correct :**
1. Posons $x$ le nombre de lapins et $y$ le nombre de poulets.
2. Système d'équations :
   - $x + y = 35$ (têtes)
   - $4x + 2y = 94$ (pattes)
3. De l'équation 1 : $y = 35 - x$.
4. Substitution dans l'équation 2 : $4x + 2(35 - x) = 94$.
5. $4x + 70 - 2x = 94 \Rightarrow 2

## Vérification
auto=True — nombres trouvés=[25.0, 2.0, 35.0, 70.0, 24.0, 94.0] (attendu 12)
