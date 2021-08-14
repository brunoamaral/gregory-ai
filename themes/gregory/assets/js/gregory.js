document.addEventListener('DOMContentLoaded', (event) => {
    const queries = document.querySelectorAll('h3')

    function searchArticles(term) {
        // let list = document.querySelector('ol.articles.'+ term );
        let articleRequest = new Request('https://api.brunoamaral.net/articles/keyword/' + term);

        fetch(articleRequest).then(response => response.json()).then(data => { console.log(data) })

        fetch(articleRequest)
            .then(response => response.json())
            .then(data => {
                let list = document.querySelector('ol.articles.' + term);
                for (const item of data) {
                    let listItem = document.createElement('li');
                    listItem.appendChild(
                        document.createElement('strong')
                    ).textContent = item.title;

                    let a = listItem.appendChild(document.createElement('a'));
                    a.textContent = `${item.source}`;
                    a.href = `${item.link}`;
                    list.appendChild(listItem);
                }
            })
            .catch(console.error);
    }

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
                    listItem.appendChild(
                        document.createElement('strong')
                    ).textContent = item.title;

                    let a = listItem.appendChild(document.createElement('a'));
                    a.textContent = `${item.source}`;
                    a.href = `${item.link}`;
                    list.appendChild(listItem);
                }
            })
            .catch(console.error);
    }



    for (let i = 0; i < queries.length; i++) {
        // console.log(queries[i].id)
        searchArticles(queries[i].id)
        searchTrials(queries[i].id)
    }
});