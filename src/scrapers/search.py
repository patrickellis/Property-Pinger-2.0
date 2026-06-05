import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin, urlparse, urlencode, parse_qsl, urlunparse
import logging

def fetch_search_results(base_search_url: str, api_key: str, max_pages: int = 10) -> list[str]:
    """
    Paginates through Rightmove search results up to `max_pages`.
    (10 pages = 240 properties per search area).
    """
    all_property_urls = []
    
    for page in range(max_pages):
        index = page * 24
        parsed = urlparse(base_search_url)

        query_params = parse_qsl(parsed.query)
        query_params = [(k, v) for k, v in query_params if k != 'index']
        query_params.append(('index', str(index)))
        new_query = urlencode(query_params)
        paginated_url = urlunparse(parsed._replace(query=new_query))
        encoded_url = quote(paginated_url)
        proxy_url = f"http://api.scraperapi.com?api_key={api_key}&url={encoded_url}&premium=true"
        
        try:
            response = requests.get(proxy_url, timeout=45)
            response.raise_for_status()
        except requests.RequestException as e:
            logging.error(f"Failed to fetch search page {page}: {e}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        new_urls_found = 0
        
        for link in soup.find_all('a', class_='propertyCard-link'):
            href = link.get('href')
            if href and 'properties' in href:
                clean_url = urljoin("https://www.rightmove.co.uk", href).split('#')[0].split('?')[0] 
                if clean_url not in all_property_urls:
                    all_property_urls.append(clean_url)
                    new_urls_found += 1
                    
        logging.info(f"Page {page + 1}: Extracted {new_urls_found} new properties.")
        
        # If Rightmove returns fewer than 24 properties on a page, it's the last page
        if new_urls_found < 24:
            logging.info("Reached the end of the search results.")
            break
            
    return all_property_urls
