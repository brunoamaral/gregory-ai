import scrapy


class JournalsSpider(scrapy.Spider):
	name = "journals"

	def start_requests(self):
		urls = [
			'https://journals.sagepub.com/action/doSearch?AllField=multiple+sclerosis&SeriesKey=msja&content=articlesChapters&countTerms=true&target=default&sortBy=Ppub&startPage=&ContentItemType=research-article'
		]
		for url in urls:
			yield scrapy.Request(url=url, callback=self.parse)

	def parse(self, response):
		page = response.url.split("/")[-2]
		filename = f'journals-{page}.html'
		with open(filename, 'wb') as f:
			f.write(response.body)
		self.log(f'Saved file {filename}')
