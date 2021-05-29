# Thank you to

@[Jneves](https://github.com/jneves) for help with the build script    
@[Melo](https://github.com/melo) for showing me [Hugo](https://github.com/gohugoio/hugo)    
@[RainerChiang](https://github.com/RainerChiang) for the [Simplesness theme](https://github.com/RainerChiang/simpleness)    
@[Rcarmo](https://github.com/rcarmo) for showing me [Node-RED](https://github.com/node-red/node-red)       

And the Lobsters at [One Over Zero](https://github.com/oneoverzero)


# Changelog

## 1.2

- Fixes #5 and #8
- Cleans up the build script a bit
- Organizes theme files to help development

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
