import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin, urlparse, urlencode, parse_qsl, urlunparse
import logging
from tenacity import retry, wait_exponential, stop_after_attempt

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5))
def fetch_search_results(base_search_url: str, api_key: str, known_property_ids: set[str] = None, max_unseen_properties: int = 50) -> list[str]:
    if "zoopla.co.uk" in base_search_url:
        return _fetch_zoopla_search_results(base_search_url, api_key, known_property_ids, max_unseen_properties)
    else:
        return _fetch_rightmove_search_results(base_search_url, api_key, known_property_ids, max_unseen_properties)

def _fetch_zoopla_search_results(base_search_url: str, api_key: str, known_property_ids: set[str] = None, max_unseen_properties: int = 50) -> list[str]:
    """
    Paginates through Zoopla search results until it finds `max_unseen_properties` 
    that are not already in the database.
    """
    all_property_urls = []
    unseen_count = 0
    known_property_ids = known_property_ids or set()
    
    # Zoopla caps results typically after 40 pages (like Rightmove's 42)
    for page in range(1, 42):
        parsed = urlparse(base_search_url)
        
        # Force List View for Zoopla (map view doesn't render cards in HTML)
        new_path = parsed.path.replace('/map/property/', '/property/').replace('/map/', '/property/')
        parsed = parsed._replace(path=new_path)
        
        query_params = parse_qsl(parsed.query)
        query_params = [(k, v) for k, v in query_params if k not in ('pn', 'results_sort')]
        query_params.append(('results_sort', 'newest_listings'))
        if page > 1:
            query_params.append(('pn', str(page)))
        
        new_query = urlencode(query_params)
        paginated_url = urlunparse(parsed._replace(query=new_query))
        encoded_url = quote(paginated_url)
        proxy_url = f"http://api.scraperapi.com?api_key={api_key}&url={encoded_url}&ultra_premium=true"
        
        try:
            response = requests.get(proxy_url, timeout=45)
            response.raise_for_status()
        except requests.RequestException as e:
            logging.error(f"Failed to fetch Zoopla search page {page}: {e}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        new_urls_found = 0
        new_unseen_on_page = 0
        page_urls = set()
        
        # Zoopla listing links typically have 'data-testid="listing-details-link"' or similar, or just match '/to-rent/details/'
        for link in soup.find_all('a', href=True):
            href = link.get('href')
            if href and ('/to-rent/details/' in href or '/property/' in href) and not href.startswith('#'):
                # Handle relative URLs correctly
                if href.startswith('/'):
                    clean_url = urljoin("https://www.zoopla.co.uk", href).split('#')[0].split('?')[0]
                elif "zoopla.co.uk" in href:
                    clean_url = href.split('#')[0].split('?')[0]
                else:
                    continue
                
                # Check it has digits (an ID) in the URL to avoid false positive links
                if re.search(r'\d{6,}', clean_url):
                    clean_url = clean_url.replace('/contact/', '/')
                    page_urls.add(clean_url)

        for clean_url in page_urls:
            if clean_url not in all_property_urls:
                all_property_urls.append(clean_url)
                new_urls_found += 1
                
                # Zoopla IDs are usually 8-digit numbers at the end
                match = re.search(r'/(\d{6,})', clean_url)
                if match and match.group(1) not in known_property_ids:
                    unseen_count += 1
                    new_unseen_on_page += 1
                    
        logging.info(f"Zoopla Page {page}: Extracted {new_urls_found} new properties ({unseen_count}/{max_unseen_properties} unseen quota met).")
        
        if unseen_count >= max_unseen_properties:
            logging.info(f"Reached unseen properties quota ({max_unseen_properties}). Stopping Zoopla pagination.")
            break

        if len(page_urls) > 0 and new_unseen_on_page == 0:
            logging.info(f"All properties on Zoopla Page {page} are already known. Stopping pagination early.")
            break
        
        # If no properties found on page, we reached the end
        if len(page_urls) == 0:
            logging.info("Reached the end of the Zoopla search results.")
            break
            
    return all_property_urls

def _fetch_rightmove_search_results(base_search_url: str, api_key: str, known_property_ids: set[str] = None, max_unseen_properties: int = 50) -> list[str]:
    """
    Paginates through Rightmove search results until it finds `max_unseen_properties` 
    that are not already in the database (or hits Rightmove's 42 page limit).
    """
    all_property_urls = []
    unseen_count = 0
    known_property_ids = known_property_ids or set()
    
    # Rightmove caps results at 1000 properties (42 pages)
    for page in range(42):
        index = page * 24
        parsed = urlparse(base_search_url)

        # Force List View (Map view returns a completely different DOM without property cards)
        new_path = parsed.path.replace('map.html', 'find.html')
        parsed = parsed._replace(path=new_path)

        query_params = parse_qsl(parsed.query)
        query_params = [(k, v) for k, v in query_params if k not in ('index', 'viewType', 'sortType')]
        query_params.append(('index', str(index)))
        query_params.append(('viewType', 'LIST'))
        query_params.append(('sortType', '6'))
        
        new_query = urlencode(query_params)
        paginated_url = urlunparse(parsed._replace(query=new_query))
        encoded_url = quote(paginated_url)
        proxy_url = f"http://api.scraperapi.com?api_key={api_key}&url={encoded_url}&premium=true"
        
        try:
            response = requests.get(proxy_url, timeout=45)
            response.raise_for_status()
        except requests.RequestException as e:
            logging.error(f"Failed to fetch Rightmove search page {page}: {e}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        new_urls_found = 0
        new_unseen_on_page = 0
        page_urls = set()
        
        for link in soup.find_all('a', class_='propertyCard-link'):
            href = link.get('href')
            if href and 'properties' in href:
                clean_url = urljoin("https://www.rightmove.co.uk", href).split('#')[0].split('?')[0] 
                page_urls.add(clean_url)

        for clean_url in page_urls:
            if clean_url not in all_property_urls:
                all_property_urls.append(clean_url)
                new_urls_found += 1
                
                # Check if this property is unseen
                match = re.search(r'/properties/(\d+)', clean_url)
                if match and match.group(1) not in known_property_ids:
                    unseen_count += 1
                    new_unseen_on_page += 1
                    
        logging.info(f"Rightmove Page {page + 1}: Extracted {new_urls_found} new properties ({unseen_count}/{max_unseen_properties} unseen quota met).")
        
        if unseen_count >= max_unseen_properties:
            logging.info(f"Reached unseen properties quota ({max_unseen_properties}). Stopping Rightmove pagination.")
            break

        if len(page_urls) > 0 and new_unseen_on_page == 0:
            logging.info(f"All properties on Rightmove Page {page + 1} are already known. Stopping pagination early.")
            break
        
        # If Rightmove returns fewer than 24 properties on a page, it's the last page
        if len(page_urls) < 24:
            logging.info("Reached the end of the Rightmove search results.")
            break
            
    return all_property_urls
