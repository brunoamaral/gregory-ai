import scrapy

class SagepubSpider(scrapy.Spider):

	name = "sagepub"
	url = 'https://journals.sagepub.com/action/doSearch?AllField=multiple+sclerosis&SeriesKey=msja&content=articlesChapters&countTerms=true&target=default&sortBy=Ppub&startPage=&ContentItemType=research-article'

	def start_requests(self,url=url):
		yield scrapy.Request(url=url, callback=self.parse)

	def parse(self, response, url=url):
		page = response.url.split("/")[-2]
		filename = f'journals-{page}.html'
		with open(filename, 'wb') as f:
			f.write(response.body)
		self.log(f'Saved file {filename}')
		for article in response.css('.searchResultItem'):
			title = article.css('h2').get()
			link = article.css('h2>span>a::attr(href)').get()
			yield {
				'title': title,
				'link': link,
			}
		