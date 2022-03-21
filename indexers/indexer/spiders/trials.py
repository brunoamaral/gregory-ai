import scrapy

class TrialsSpider(scrapy.Spider):
	name = 'trials'
	import psycopg2
	from dotenv import load_dotenv
	import os

	load_dotenv()
	## GET ENV
	db_host = os.getenv('DB_HOST')
	postgres_user = os.getenv('POSTGRES_USER')
	postgres_password = os.getenv('POSTGRES_PASSWORD')
	postgres_db = os.getenv('POSTGRES_DB')

	try:
		conn = psycopg2.connect("dbname='"+ postgres_db +"' user='" + postgres_user + "' host='" + db_host + "' password='" + postgres_password + "'")
	except:
		print("I am unable to connect to the database")


	###
	# GET TRIALS
	###

	cur = conn.cursor()
	cur.execute("""
		SELECT source_id,name,link,items_selector,title_selector,link_selector FROM sources WHERE method = 'scrape' AND source_for = 'trials';
	""")
	sources = cur.fetchall()

	source_id = None

	link = None
	items_selector = None
	title_selector = None
	link_selector = None

	for i in sources:
		source_id=i[0]
		source=i[1]
		link=i[2]
		items_selector=i[3]
		title_selector=i[4]
		link_selector=i[5]
		
		url = link

		def start_requests(self,url=url):
			yield scrapy.Request(url=url, callback=self.parse)

		def parse(self, response, url=url, items_selector=items_selector, title_selector=title_selector, link_selector=link_selector, source=source):
			page = response.url.split("/")[-2]	
			filename = f'journals-{page}.html'
			with open(filename, 'wb') as f:
				f.write(response.body)
			self.log(f'Saved file {filename}')
			for trial in response.css(items_selector):
				title = trial.css(title_selector).get()
				link = trial.css(link_selector).get()
				yield {
					'source': source,
					'title': title,
					'link': url + link,
				}
		