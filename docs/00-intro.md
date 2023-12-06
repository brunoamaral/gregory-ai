# Gregory, an AI to assist research and monitor science

Gregory is an Artificial Intelligence (AI) to index and identify relevant content from any number of sources.

It uses Machine Learning (ML) to predict relevancy. And includes a [Metabase](https://metabase.com/) instance to classify and quantify information.

A full version is available at [Gregory-MS.com](https://gregory-ms.com), where we have indexed over 16,000 articles on Multiple Sclerosis Research to assist Neurologists and Researchers.

## Features

- Machine Learning prediction of relevant content
- Key takeaways extracted using Artificial Intelligence
- Configure RSS feeds to gather search results for any keyword
- Configure searches on any public website, not just medical publications
- Set up automatic email notifications
- Manage and segment subscribers
- Public and Private API  
- Customize your own categories to get personalized search results
- Breakdown broader subjects into different segments
- Identify and subscribe to authors

## Use Cases

### For researchers of any field

Gregory can act as a research assistant by fetching the latest articles and organising them into categories. These categories can then be plotted in a chart to see which need further research, and which are “too crowded”.

#### Jobs to be done

- Literature review
- Map out areas of current research
- Identify Research trends

### For healthcare professionals of any specialty

Using the right sources, it’s possible to have Gregory stay up to date with what is being published about any disease, condition, or medication. Turn it into an observatory for one or more subjects.

#### Jobs to be done

- Get relevant information in real time
- Track research around specific therapeutics

### For everyone filtering for important news

With the machine learning algorithm, Gregory learns what is important to its users and cut the noise from the river of news . 
By creating an email list, teams can receive a digest of the relevant articles found, or real time notifications.

This allows you to chart the news and identify trends with greater ease.

#### Jobs to be done

- Real time notification of relevant news (no Fear Of Missing Out)
- Identify trends

### Build a central point of knowledge

Developers can use the Application Protocol Interface (API) to connect Gregory to any system, displaying the articles filtered by any query, or embedding the charts.

### Keep everyone in the loop

The email notifications can be sent to any email address, without the need for a registered user account.

#### Jobs to be done

- Send newsletters or other digests to teams
- Feed a website with curated information in real time

## How it works


Gregory's workflow:

```mermaid
flowchart LR;
 SourcesA(fa:fa-newspaper Source A) --> DB[("Gregory Database")];
 SourcesB(fa:fa-newspaper Source B) --> DB;
 SourcesC(fa:fa-newspaper Source C) --> DB;
 DB --> Model("Machine Learning Prediction");
 Model --> RF{{"Human Feedback"}};
 RF --> Model;
 RF --> PD{{"Digest"}};
 PD --> site("Website")
 PD --> email("Email")
```



