from django.db import migrations
import re


def normalize_orcid(value):
	"""Normalize ORCID values by removing URL prefixes and trailing slashes.
	Returns None when input is falsy after cleanup.
	"""
	if not value:
		return None
	s = str(value).strip()
	s = re.sub(r'^(https?://)?(www\.)?orcid\.org/', '', s, flags=re.IGNORECASE)
	s = s.strip().strip('/')
	return s or None


def forwards(apps, schema_editor):
	Authors = apps.get_model('gregory', 'Authors')
	Articles = apps.get_model('gregory', 'Articles')
	through = Articles.authors.through

	# Build groups by normalized ORCID
	normalized_groups = {}
	for author in Authors.objects.exclude(ORCID__isnull=True).exclude(ORCID=''):
		norm = normalize_orcid(author.ORCID)
		if not norm:
			continue
		normalized_groups.setdefault(norm, []).append(author)

	# First, handle duplicates by merging into a single keeper per ORCID
	for norm, group in normalized_groups.items():
		if len(group) <= 1:
			continue
		# Choose keeper: author with most article links, fallback to smallest pk
		def article_count(a):
			return through.objects.filter(authors_id=a.pk).count()
		keeper = max(group, key=lambda a: (article_count(a), -a.pk))
		for a in group:
			if a.pk == keeper.pk:
				continue
			
			# Get all article relationships for this duplicate author
			duplicate_relationships = through.objects.filter(authors_id=a.pk)
			
			for rel in duplicate_relationships:
				article_id = rel.articles_id
				
				# Check if relationship already exists with keeper
				existing_rel = through.objects.filter(
					articles_id=article_id, 
					authors_id=keeper.pk
				).first()
				
				if existing_rel:
					# Relationship already exists, just delete the duplicate
					rel.delete()
				else:
					# Update to point to keeper
					rel.authors_id = keeper.pk
					rel.save()
			
			# Delete duplicate author
			a.delete()
		# Ensure keeper has normalized ORCID
		if keeper.ORCID != norm:
			keeper.ORCID = norm
			keeper.save(update_fields=['ORCID'])

	# Then, normalize remaining (non-duplicate) records
	for author in Authors.objects.exclude(ORCID__isnull=True).exclude(ORCID=''):
		norm = normalize_orcid(author.ORCID)
		if norm and norm != author.ORCID:
			# If this would conflict due to a remaining author with same ORCID, skip (already merged above)
			if Authors.objects.filter(ORCID=norm).exclude(pk=author.pk).exists():
				continue
			author.ORCID = norm
			author.save(update_fields=['ORCID'])


def backwards(apps, schema_editor):
	# No-op: cannot reliably restore the previous string format or duplicates
	pass


class Migration(migrations.Migration):

	dependencies = [
		('gregory', '0025_add_ml_consensus_type_to_subject'),
	]

	operations = [
		migrations.RunPython(forwards, backwards),
	]
