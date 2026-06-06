import fs from 'fs';
import yaml from 'js-yaml';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function geocode(address) {
  const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(address)}&format=json&limit=1`;
  try {
    const res = await fetch(url, { headers: { 'User-Agent': 'PropertyPinger/1.0' } });
    const data = await res.json();
    if (data && data.length > 0) {
      return { lat: parseFloat(data[0].lat), lng: parseFloat(data[0].lon) };
    }
  } catch (e) {
    console.error(`Failed to geocode ${address}`, e);
  }
  return null;
}

async function run() {
  const fileContents = fs.readFileSync(path.join(__dirname, '../config/london.yaml'), 'utf8');
  const data = yaml.load(fileContents);
  
  const pois = [];
  const hubs = data.locations?.hubs || [];
  const venues = data.locations?.venues || [];

  for (const h of hubs) {
    const coords = await geocode(h);
    if (coords) {
      pois.push({ name: h, type: 'hub', lat: coords.lat, lng: coords.lng });
    }
    await sleep(1500); // Respect nominatim rate limit
  }

  for (const v of venues) {
    const coords = await geocode(v);
    if (coords) {
      pois.push({ name: v, type: 'venue', lat: coords.lat, lng: coords.lng });
    }
    await sleep(1500);
  }

  fs.writeFileSync(path.join(__dirname, 'src/pois.json'), JSON.stringify(pois, null, 2));
  console.log(`Geocoded ${pois.length} POIs`);
}

run().catch(console.error);
