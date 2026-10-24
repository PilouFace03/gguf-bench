# gen-en-code (code)

## Consigne
Write a Python function `is_palindrome(s: str) -> bool` that returns True if s reads the same forwards and backwards, ignoring case, spaces and punctuation. Output only the code, with no explanation and no markdown fences.

## Réponse brute
def is_palindrome(s: str) -> bool:
    """
    Checks if a string reads the same forwards and backwards, ignoring case,
    spaces, and punctuation.
    
    Args:
        s (str): The input string to check.
        
    Returns:
        bool: True if the string is a palindrome, False otherwise.
    """
    # Convert to lowercase and strip non-alphanumeric characters
    cleaned = ''.join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]

## Vérification
auto=True — tests OK
