document.addEventListener('DOMContentLoaded', (event) => {

    const queries = document.querySelectorAll('h3')
    const nounphrases = document.querySelectorAll('li.phrase')
    const commonwords = [
        'Characteristics',
        'ms',
        'multiple sclerosis',
        'patients',
        'persons',
        'recovery'
    ]
    let related = [];


    for (const element of nounphrases) {
        let test = element.textContent.toLowerCase()
        if (commonwords.includes(test) == false) {
            related.push(element.textContent);
        }

    }

    function searchArticles(term) {

        let articleRequest = new Request('https://api.brunoamaral.net/articles/keyword/' + term);

        // fetch(articleRequest).then(response => response.json()).then(data => { console.log(data) })

        fetch(articleRequest)
            .then(response => response.json())
            .then(data => {
                let list = document.querySelector('ol.articles.' + term);
                for (const item of data) {
                    let listItem = document.createElement('li');
                    listItem.textContent = item.title + ' ';

                    let a = listItem.appendChild(document.createElement('a'));
                    a.textContent = `${item.source}`;
                    a.href = `${item.link}`;
                    list.appendChild(listItem);
                }
            })
            .catch(console.error);
    };

    function searchTrials(term) {
        // let list = document.querySelector('ol.trials.'+ term );

        let trialsRequest = new Request('https://api.brunoamaral.net/trials/keyword/' + term);
        fetch(trialsRequest).then(response => response.json()).then(data => { console.log(data) })

        fetch(trialsRequest)
            .then(response => response.json())
            .then(data => {
                let list = document.querySelector('ol.trials.' + term);
                for (const item of data) {
                    let listItem = document.createElement('li');
                    listItem.textContent = item.title + ' ';

                    let a = listItem.appendChild(document.createElement('a'));
                    a.textContent = `${item.source}`;
                    a.href = `${item.link}`;
                    list.appendChild(listItem);
                }
            })
            .catch(console.error);
    };

    function searchRelated(term) {
        // var request = new Request(url:'/',method:"POST",body:"{"keywords":["1","2"]} );
        let postdata = { "keywords": term }
            // console.log(postdata)
        let relatedRequest = new Request("https://api.brunoamaral.net/articles/related", {
            method: "POST",
            body: JSON.stringify(postdata),
            headers: {
                'Content-Type': 'application/json'
            }
        })

        fetch(relatedRequest)
            .then(response => response.json())
            .then(data => {
                console.log(data)
                let list = document.querySelector('ol#relatedarticles')

                for (const item of data) {
                    // console.log(item)
                    let listItem = document.createElement('li');
                    listItem.textContent = item.title + ' ';

                    let a = listItem.appendChild(document.createElement('a'));
                    a.textContent = `${item.source}`;
                    a.href = `${item.link}`;
                    list.appendChild(listItem);
                }
            })
            .catch(console.error);
    };





    for (let i = 0; i < queries.length; i++) {
        // console.log(queries[i].id)
        searchArticles(queries[i].id)
        searchTrials(queries[i].id)
    };
    searchRelated(related);

});