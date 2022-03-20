import scrapy

class TrialsSpider(scrapy.Spider):

	name = "novartis"
	url = 'https://www.recruiting-trials.novartis.com/?condition=1691&phase=All'

	def start_requests(self,url=url):
		yield scrapy.Request(url=url, callback=self.parse)

	def parse(self, response, url=url):
		page = response.url.split("/")[-2]
		filename = f'journals-{page}.html'
		with open(filename, 'wb') as f:
			f.write(response.body)
		self.log(f'Saved file {filename}')
		for trial in response.css('td.views-field-title'):
			title = trial.css('td.views-field>a::text').get()
			link = trial.css('td.views-field>a::attr(href)').get()
			yield {
				'title': title,
				'link': url + link,
			}
		