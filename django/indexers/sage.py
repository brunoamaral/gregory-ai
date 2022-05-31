from gregory.models import Articles
from datetime import datetime
import json

f = open('/code/indexers/input/sagepub.json')
input = json.load(f)

discovery_date = datetime.now()
for i in input:
	try: 
		# print(i)
		title = i['title']
		link = i['link']
		doi = i['doi']
		source = 9
		article = Articles.objects.create(
			title = title,
			link = link,
			doi = doi,
			discovery_date = discovery_date,
			source = source,
		)
	except:
		print('not unique?')
		pass