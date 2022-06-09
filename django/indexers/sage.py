from gregory.models import Articles, Sources
from datetime import datetime
import json

f = open('/code/indexers/input/sagepub-1995-2010.json')
input = json.load(f)

discovery_date = datetime.now()
source = Sources.objects.get(pk=9)
for i in input:
	try: 
		# print(i)
		title = i['title']
		link = i['link']
		doi = i['doi']
		article = Articles.objects.create(
			title = title,
			link = link,
			doi = doi,
			discovery_date = discovery_date,
			source = source,
		)
	except:
		print('not unique?::', doi, title)
		pass
