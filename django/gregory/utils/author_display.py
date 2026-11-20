def author_name(author):
	"""An author's display name: the stored full name, else given plus family name."""
	return author.full_name or f"{author.given_name} {author.family_name}".strip()
