"""Serializers for ``/labs/``. Built field by field: a lab row also holds review state,
notes and the raw link flags, and an author row holds affiliation and email fields,
none of which belong in a public response."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.labs import lab_display_name
from gregory.models import Authors, Institution, Lab, LabCategory, LabMembership, LabSubject
from gregory.utils.author_display import author_name


class LabPersonSerializer(serializers.ModelSerializer):
	"""The lab's head."""

	full_name = serializers.SerializerMethodField()
	orcid = serializers.CharField(source="ORCID", allow_null=True, read_only=True)

	class Meta:
		model = Authors
		fields = ["author_id", "full_name", "orcid"]

	def get_full_name(self, obj) -> str:
		return author_name(obj)

	def to_representation(self, instance):
		data = super().to_representation(instance)
		data["orcid"] = data["orcid"] or None
		return data


class LabMemberSerializer(serializers.ModelSerializer):
	"""A LabMembership row, shown as the person it points to."""

	author_id = serializers.IntegerField(read_only=True)
	full_name = serializers.SerializerMethodField()
	orcid = serializers.CharField(source="author.ORCID", allow_null=True, read_only=True)

	class Meta:
		model = LabMembership
		fields = ["author_id", "full_name", "orcid"]

	def get_full_name(self, obj) -> str:
		return author_name(obj.author)

	def to_representation(self, instance):
		data = super().to_representation(instance)
		data["orcid"] = data["orcid"] or None
		return data


class LabInstitutionSerializer(serializers.ModelSerializer):
	name = serializers.CharField(read_only=True)
	country_code = serializers.CharField(allow_null=True, read_only=True)
	openalex_id = serializers.CharField(allow_null=True, read_only=True)
	ror = serializers.CharField(allow_null=True, read_only=True)

	class Meta:
		model = Institution
		fields = ["name", "country_code", "openalex_id", "ror"]

	def to_representation(self, instance):
		data = super().to_representation(instance)
		for key in ("country_code", "openalex_id", "ror"):
			data[key] = data[key] or None
		return data


class _LabTopicLinkSerializer(serializers.ModelSerializer):
	articles = serializers.IntegerField(source="article_count", read_only=True)
	relevant_articles = serializers.IntegerField(source="relevant_count", read_only=True)
	recent_articles = serializers.IntegerField(source="recent_article_count", read_only=True)
	latest_article_year = serializers.IntegerField(allow_null=True, read_only=True)


class LabSubjectLinkSerializer(_LabTopicLinkSerializer):
	id = serializers.IntegerField(source="subject_id", read_only=True)
	name = serializers.CharField(source="subject.subject_name", read_only=True)

	class Meta:
		model = LabSubject
		fields = ["id", "name", "articles", "relevant_articles", "recent_articles", "latest_article_year"]


class LabCategoryLinkSerializer(_LabTopicLinkSerializer):
	id = serializers.IntegerField(source="category_id", read_only=True)
	name = serializers.CharField(source="category.category_name", read_only=True)
	slug = serializers.CharField(source="category.category_slug", read_only=True)

	class Meta:
		model = LabCategory
		fields = ["id", "name", "slug", "articles", "relevant_articles", "recent_articles", "latest_article_year"]


class LabSerializer(serializers.ModelSerializer):
	"""A lab as the public API shows it. Expects the queryset built by ``LabViewSet``:
	the visible links and the members arrive prefetched and already scoped to the caller."""

	name = serializers.SerializerMethodField()
	curated = serializers.BooleanField(source="is_manual", read_only=True)
	head = LabPersonSerializer(read_only=True, allow_null=True)
	institution = LabInstitutionSerializer(read_only=True, allow_null=True)
	members = LabMemberSerializer(source="member_rows", many=True, read_only=True)
	subjects = LabSubjectLinkSerializer(source="visible_subject_links", many=True, read_only=True)
	categories = LabCategoryLinkSerializer(source="visible_category_links", many=True, read_only=True)
	topics_last_changed = serializers.SerializerMethodField()

	class Meta:
		model = Lab
		fields = [
			"id",
			"name",
			"curated",
			"head",
			"institution",
			"member_count",
			"members",
			"subjects",
			"categories",
			"topics_last_changed",
		]

	def get_name(self, obj) -> str:
		return getattr(obj, "display_name", None) or lab_display_name(obj)

	@extend_schema_field({"type": "string", "format": "date-time", "nullable": True})
	def get_topics_last_changed(self, obj):
		stamps = [
			link.computed_at
			for link in (*obj.visible_subject_links, *obj.visible_category_links)
			if link.computed_at
		]
		if not stamps:
			return None
		return serializers.DateTimeField().to_representation(max(stamps))


class AuthorLabHeadSerializer(serializers.Serializer):
	"""The head, as shown inside an author's ``lab`` summary."""

	author_id = serializers.IntegerField()
	full_name = serializers.CharField()


class AuthorLabSerializer(serializers.Serializer):
	"""The author's lab, a summary: fetch ``/labs/{id}/`` for the rest."""

	id = serializers.IntegerField()
	name = serializers.CharField()
	role = serializers.ChoiceField(choices=["head", "member"])
	head = AuthorLabHeadSerializer(allow_null=True)
