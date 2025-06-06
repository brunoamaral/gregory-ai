---
title: "brunoamaral/gregory-ai: Artificial Intelligence and Machine Learning to accelerate scientific research and filter relevant content"
source: "https://github.com/brunoamaral/gregory-ai/issues/413"
author:
  - "[[GitHub]]"
published:
created: 2025-06-04
description: "Artificial Intelligence and Machine Learning to accelerate scientific research and filter relevant content - brunoamaral/gregory-ai"
tags:
  - "clippings"
---
rss feed where the problem was found: [https://pubmed.ncbi.nlm.nih.gov/rss/search/10guX6I3SqrbUeeLKSTD6FCRM44ewnrN2MKKTQLLPMHB4xNsZU/?limit=15&utm\_campaign=pubmed-2&fc=20210216052009](https://pubmed.ncbi.nlm.nih.gov/rss/search/10guX6I3SqrbUeeLKSTD6FCRM44ewnrN2MKKTQLLPMHB4xNsZU/?limit=15&utm_campaign=pubmed-2&fc=20210216052009)


## problem
when the source rss feed is from pubmed, some values of article.summary are cut off or incomplete.

## Examples

```csv
article_id, title, summary, published
296111,"CONCLUSIONS: Overall, roflumilast could be used as a lead compound for developing a novel multifunctional therapeutic drug used for the prevention of HAE or thrombotic disorders.","2025-06-04 17:28:43.888758+00"
```

### Expected behavior

summary should be the text below:

```
Background: This study aimed to evaluate the effects of selected phosphodiesterase-4 inhibitors (PDE-4 inhibitors)-roflumilast, ibudilast, and crisaborole-on the activity of blood coagulation factor XII (FXII). In the intrinsic coagulation pathway, FXII is known to initiate the kallikrein-kinin system (KKS), causing an increase in the system expression, which ultimately leads to inflammation and coagulation states. Additionally, the activation of KKS downstream effectors leads to inflammation. Inflammation signaling was found to be initiated when the bradykinin (BK) protein binds to its B2 receptor because of the FXII-dependent pathway activation. BK abnormalities can cause a critical condition, hereditary angioedema (HAE), which is characterized by recurring serious swelling. While it is considered unnecessary for hemostasis, FXII is an important enzyme for pathogenic thrombosis. Because of this special characteristic, FXII is a desirable therapeutic target. Our hypothesis is to identify the inhibitory effects of roflumilast, ibudilast, and crisaborole on the activated FXII and to reveal their beneficial impacts in the reduction of the pathogenesis of FXII-related conditions, HAE, and thrombosis. In a current study, we presented the inhibitory effect of tested drugs on the main target activated factor XII (FXIIa) as well as two other plasma protease enzymes included in the target pathway, plasma kallikrein and FXIa.

Methods: To achieve our aim, in vitro chromogenic enzymatic assays were utilized to assess the inhibitory effects of these drugs by monitoring the amount of para-nitroaniline (pNA) chromophore released from the substrate of FXIIa, FXIa, or plasma kallikrein.

Results: Our study findings exhibited that among assessed PDE-4 inhibitor drugs, roflumilast at micromolar concentrations significantly inhibited FXIIa in a dose-dependent manner. The FXIIa was clearly suppressed by roflumilast, but not the other related KKS members, plasma kallikrein, or the activated factor XI. On the other hand, ibudilast and crisaborole showed no inhibitory effects on the activities of all enzymes.

Conclusions: Overall, roflumilast could be used as a lead compound for developing a novel multifunctional therapeutic drug used for the prevention of HAE or thrombotic disorders.

Keywords: PDE-4 inhibitors; blood coagulation; factor XIIa; in silico; in vitro; roflumilast. 
```

## Hypothesis

1. there is a problem in the feedreder part of the pipeline.
2. the article was updated after we fetched the feed, and the summary was updated.

## Plan

1. review the feedreader for articles to find possible issues with the summary field
	 - check if the summary is being truncated or if there is a limit on the length of the summary being fetched
	 - ensure that the summary is being fetched correctly from the source
2. add a quick log for articles that have a summary more than X but less than Y characters long