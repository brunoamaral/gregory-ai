import feedparser
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()
## GET ENV
db_host = os.getenv('DB_HOST')
# db_host = 'localhost'
postgres_user = os.getenv('POSTGRES_USER')
postgres_password = os.getenv('POSTGRES_PASSWORD')
postgres_db = os.getenv('POSTGRES_DB')

try:
	conn = psycopg2.connect("dbname='"+ postgres_db +"' user='" + postgres_user + "' host='" + db_host + "' password='" + postgres_password + "'")
except:
	print("I am unable to connect to the database")

cur = conn.cursor()
cur.execute("SELECT source_id,name,link,subject FROM sources WHERE method = 'rss';")
sources = cur.fetchall()

for i in sources:
	link = i[2]
	source_id = i[0]
	d = feedparser.parse(link)
	for entry in d['entries']:
		summary = ''
		if hasattr(entry,'summary_detail'):
			summary = entry['summary_detail']['value']
		if hasattr(entry,'summary'):
			summary = entry['summary']
		with conn:
			try:
				cur.execute("""
				INSERT INTO articles (discovery_date,title,summary,link,published_date,source)
				VALUES ( current_timestamp, %(article_title)s, %(article_summary)s, %(article_link)s, %(article_pubdate)s, %(source_id)s );
					""",
					{'article_title': entry['title'], 'article_summary': summary, 'article_link': entry['link'], 'article_pubdate': entry['published'], 'source_link': link, 'source_id': source_id })
				print(entry['title'])
			except Exception as e: print(e)
			finally:
				if conn.closed == 1:
					conn.close()