from gregory.models import Articles, Sources
from datetime import datetime
import json
import logging

f = open("/code/indexers/input/sagepub.json")
input = json.load(f)

discovery_date = datetime.now()
source = Sources.objects.get(pk=9)
for i in input:
	try:
		# print(i)
		title = i["title"]
		link = i["link"]
		doi = i["doi"]
		article = Articles.objects.create(
			title=title,
			link=link,
			doi=doi,
			discovery_date=discovery_date,
			source=source,
		)
	except:  # noqa: E722
		logging.warning("not unique?:: %s %s", doi, title)
		pass
