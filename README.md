# Live Version

https://labs.brunoamaral.eu

# Gregory
Gregory aggregates searches in JSON and outputs to a Hugo static site

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