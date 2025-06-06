# Feedreader Articles Processing Flow

```mermaid
flowchart TD
    A[Start: handle method] --> B[setup method]
    B --> C[Get site settings & etiquette]
    C --> D[update_articles_from_feeds]
    
    D --> E[Get RSS sources for science papers]
    E --> F{For each source}
    F --> G[fetch_feed with SSL handling]
    
    G --> H{For each entry in feed}
    H --> I[Extract title]
    I --> J[Extract summary with priority logic]
    
    J --> K{Source is PubMed?}
    K -->|Yes| L[Use content[0]['value']]
    K -->|No| M[Use summary_detail or summary]
    
    L --> N[Clean summary with SciencePaper.clean_abstract]
    M --> N
    
    N --> O[Extract published date & link]
    O --> P[Extract DOI if available]
    
    P --> Q{DOI found?}
    Q -->|Yes| R[Create SciencePaper object]
    Q -->|No| S[Use feed summary directly]
    
    R --> T[Refresh crossref data]
    T --> U{Crossref abstract available?}
    U -->|Yes| V[Use crossref abstract]
    U -->|No| W[Use feed summary]
    
    V --> X[Clean crossref abstract]
    W --> Y[Keep feed summary]
    X --> Z[Log if summary length 20-500 chars]
    Y --> Z
    S --> AA[Log if summary length 20-500 chars]
    
    Z --> BB{Article exists in DB?}
    AA --> BB
    
    BB -->|No| CC[Create new article]
    BB -->|Yes| DD[Update existing article if changed]
    
    CC --> EE[Add teams, subjects, sources]
    DD --> FF{Has crossref authors?}
    EE --> FF
    
    FF -->|Yes| GG[Process each author]
    FF -->|No| HH[Continue to next entry]
    
    GG --> II{Author has ORCID?}
    II -->|Yes| JJ[Get/create author by ORCID]
    II -->|No| KK{Has given & family name?}
    
    KK -->|Yes| LL[Get/create author by names]
    KK -->|No| MM[Skip author]
    
    JJ --> NN[Link author to article]
    LL --> OO{Multiple authors found?}
    OO -->|Yes| PP[Use first with ORCID or first]
    OO -->|No| NN
    PP --> NN
    
    NN --> QQ{More authors?}
    QQ -->|Yes| GG
    QQ -->|No| HH
    MM --> QQ
    
    HH --> RR{More entries?}
    RR -->|Yes| H
    RR -->|No| SS{More sources?}
    SS -->|Yes| F
    SS -->|No| TT[End]

    style A fill:#e1f5fe
    style TT fill:#e8f5e8
    style K fill:#fff3e0
    style Q fill:#fff3e0
    style U fill:#fff3e0
    style BB fill:#fff3e0
    style Z fill:#ffebee
    style AA fill:#ffebee
```

## Key Components

### Summary Extraction Priority
1. **PubMed feeds**: `entry['content'][0]['value']`
2. **Other feeds**: `entry['summary_detail']['value']` → `entry['summary']`
3. **All summaries**: Cleaned with `SciencePaper.clean_abstract()`

### DOI Processing
- **PubMed**: Extract from `dc_identifier` field
- **FASEB**: Extract from `prism_doi` field
- **With DOI**: Fetch crossref metadata and use crossref abstract if available
- **No DOI**: Use feed summary directly

### Author Processing
- **Priority**: ORCID → Given/Family names
- **Deduplication**: Handle multiple authors with same names
- **Linking**: Only link if not already associated with article

### Quality Checks
- **Summary length warnings**: 20-500 characters flagged as potentially truncated
- **Database integrity**: Check for existing articles by DOI or title
- **Error handling**: Generic database error handler available
