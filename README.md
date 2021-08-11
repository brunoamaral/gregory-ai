# Gregory
Gregory aggregates searches in JSON and outputs to a Hugo static site

# Live Version

https://labs.brunoamaral.eu

# Changelog

## 1.6

New Sources Added for Clinical Trials

CUF
Novartis
New Features

A digest of new articles is sent to the Admin every 48h so that the most relevant findings can be flagged.
Weekly digest is sent to the subscribers, it lists the articles flagged by the admin.
The Admin now receives a notification of new clinical trials as they are posted.
Changes

The file notification flows.json replaces newsletter.json
The main json file with the node-red flows was cleaned up and corrected some missing links between nodes
The database schema was added to the repository as gregory_schema.sql
The full sqlite database was added to the repository as gregory.sql
A new flow was added that integrates with twitter using a Notion database
Twitter integration

Results that are flagged as relevant are posted in the account @GregroryMS_ using the service provided by Automate.io.

image

Roadmap

New sources we would like to add:

RNEC
FirstWord Pharma
EMA (CTIS system to be made available online on January 2022)
Champalimaud Foundation
CEIC (Doesn't seem to have any public database)

## 1.2

- Fixes #5 and #8
- Cleans up the build script a bit
- Organizes theme files to help development

# Install

```bash 
git clone git@github.com:brunoamaral/gregory.git;
cd gregory;
hugo mod get -u;
hugo;
```
# Node-RED Flow

`data/articles.json` and `data/trials.json` are generated from a Node-Red flow available in the `flows.json` file.

# Build script

For an example on how to build the website, see build-example.py. The server URL was hidden for the time being. 

The path /api/articles.json and /api/trials.json includes the full database export.

The same information is available in excel format: /api/articles.xlsx and /api/trials.xlsx.

# Thank you to

@[Jneves](https://github.com/jneves) for help with the build script    
@[Melo](https://github.com/melo) for showing me [Hugo](https://github.com/gohugoio/hugo)    
@[RainerChiang](https://github.com/RainerChiang) for the [Simplesness theme](https://github.com/RainerChiang/simpleness)    
@[Rcarmo](https://github.com/rcarmo) for showing me [Node-RED](https://github.com/node-red/node-red)       

And the Lobsters at [One Over Zero](https://github.com/oneoverzero)

