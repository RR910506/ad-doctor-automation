PR_LANGUAGE_PHRASES=('amazing campaign','groundbreaking initiative','game-changing','powerful campaign','takes the internet by storm','netizens are loving it','revolutionary','sets a new benchmark')
VOICE_ATTRIBUTES=('confident','analytical','curious','opinionated','strategically clear','concise','non-promotional')
def find_pr_language(text:str)->tuple[str,...]:return tuple(p for p in PR_LANGUAGE_PHRASES if p in text.lower())
