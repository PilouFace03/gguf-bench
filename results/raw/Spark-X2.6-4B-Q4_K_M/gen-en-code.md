# gen-en-code (code)

## Consigne
Write a Python function `is_palindrome(s: str) -> bool` that returns True if s reads the same forwards and backwards, ignoring case, spaces and punctuation. Output only the code, with no explanation and no markdown fences.

## Réponse brute
def is_palindrome(s: str) -> bool:
    cleaned = ''.join(char.lower() for char in s if char.isalnum())
    return cleaned == cleaned[::-1]

## Vérification
auto=True — tests OK
