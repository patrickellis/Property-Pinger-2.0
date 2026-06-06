import { useState, useEffect, useMemo } from 'react';
import { collection, getDocs } from 'firebase/firestore';
import { MapContainer, TileLayer, Marker } from 'react-leaflet';
import L from 'leaflet';
import { db } from './firebase';
import { X, CheckCircle2, XCircle, Map as MapIcon } from 'lucide-react';

interface Property {
  id: string;
  score: number;
  ignored: boolean;
  price_pcm?: number;
  bedrooms?: number;
  latitude?: number;
  longitude?: number;
  property_type?: string;
  breakdown?: {
    pros?: string[];
    cons?: string[];
    scorecard?: Record<string, number>;
  };
  raw_data?: any;
}

const createCustomIcon = (score: number, ignored: boolean) => {
  return L.divIcon({
    className: `custom-marker ${ignored ? 'ignored' : ''}`,
    html: `<div>${Math.round(score)}</div>`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
  });
};

function App() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Filters
  const [minScore, setMinScore] = useState(0);
  const [maxPrice, setMaxPrice] = useState(15000);
  const [minBeds, setMinBeds] = useState(1);
  const [showIgnored, setShowIgnored] = useState(false);
  
  // Selected Property
  const [selectedProp, setSelectedProp] = useState<Property | null>(null);

  useEffect(() => {
    const fetchProps = async () => {
      try {
        const querySnapshot = await getDocs(collection(db, 'properties'));
        const props: Property[] = [];
        querySnapshot.forEach((doc) => {
          const data = doc.data() as Property;
          
          // Fallback to raw_data for properties evaluated before the new schema
          const lat = data.latitude || data.raw_data?.latitude;
          const lng = data.longitude || data.raw_data?.longitude;
          const price = data.price_pcm || data.raw_data?.price_pcm;
          const beds = data.bedrooms || data.raw_data?.bedrooms;
          const type = data.property_type || data.raw_data?.property_type;

          if (lat && lng) {
            props.push({ 
              ...data, 
              id: doc.id,
              latitude: lat,
              longitude: lng,
              price_pcm: price,
              bedrooms: beds,
              property_type: type
            });
          }
        });
        setProperties(props);
      } catch (e: any) {
        console.error("Error fetching properties: ", e);
        setError(e.message || "Failed to fetch properties");
      } finally {
        setLoading(false);
      }
    };
    fetchProps();
  }, []);

  const filteredProperties = useMemo(() => {
    return properties.filter(p => {
      if (!showIgnored && p.ignored) return false;
      if ((p.score || 0) < minScore) return false;
      if (p.price_pcm && p.price_pcm > maxPrice) return false;
      if (p.bedrooms && p.bedrooms < minBeds) return false;
      return true;
    });
  }, [properties, minScore, maxPrice, minBeds, showIgnored]);

  return (
    <div className="dashboard-layout">
      {/* Sidebar */}
      <div className="sidebar glass">
        <h1><MapIcon size={28} /> Property Pinger</h1>
        
        <div style={{ marginTop: '20px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div className="filter-group">
            <label>Minimum Score</label>
            <input 
              type="range" min="0" max="100" value={minScore} 
              onChange={e => setMinScore(Number(e.target.value))} 
            />
            <div className="value-display">{minScore} pts</div>
          </div>
          
          <div className="filter-group">
            <label>Maximum Price</label>
            <input 
              type="range" min="1000" max="15000" step="100" value={maxPrice} 
              onChange={e => setMaxPrice(Number(e.target.value))} 
            />
            <div className="value-display">£{maxPrice} pcm</div>
          </div>
          
          <div className="filter-group">
            <label>Minimum Beds</label>
            <input 
              type="range" min="1" max="5" value={minBeds} 
              onChange={e => setMinBeds(Number(e.target.value))} 
            />
            <div className="value-display">{minBeds}+ Beds</div>
          </div>
          
          <div className="toggle-group" onClick={() => setShowIgnored(!showIgnored)}>
            <div className={`toggle-switch ${showIgnored ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Show Ignored Properties</label>
          </div>
        </div>
        
        <div style={{ marginTop: 'auto', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
          Showing {filteredProperties.length} of {properties.length} properties
        </div>
      </div>

      {/* Map */}
      <div className="map-container">
        {loading ? (
          <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ color: 'white' }}>Loading map data...</div>
          </div>
        ) : error ? (
          <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', padding: '20px', textAlign: 'center' }}>
            <div style={{ color: '#ef4444', background: 'rgba(239, 68, 68, 0.1)', padding: '20px', borderRadius: '8px', border: '1px solid #ef4444' }}>
              <h3 style={{ marginBottom: '10px' }}>Connection Error</h3>
              <p>{error}</p>
              <p style={{ marginTop: '15px', fontSize: '0.9rem', color: '#94a3b8' }}>
                If this says "Missing or insufficient permissions", you need to update your Firestore Security Rules in the Firebase Console to allow reads.
              </p>
            </div>
          </div>
        ) : (
          <MapContainer  
            center={[51.5074, -0.1278]} 
            zoom={12} 
            style={{ height: '100%', width: '100%', background: '#0f172a' }}
            zoomControl={false}
          >
            <TileLayer
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
            />
            {filteredProperties.map(p => (
              <Marker 
                key={p.id} 
                position={[p.latitude!, p.longitude!]}
                icon={createCustomIcon(p.score || 0, p.ignored)}
                eventHandlers={{
                  click: () => setSelectedProp(p),
                }}
              />
            ))}
          </MapContainer>
        )}
        
        {/* Property Drawer */}
        <div className={`property-drawer glass ${selectedProp ? 'open' : ''}`}>
          {selectedProp && (
            <>
              <div className="drawer-header">
                <h2 style={{ fontSize: '1.2rem', fontWeight: 600 }}>{selectedProp.raw_data?.display_address || 'Property Details'}</h2>
                <button className="drawer-close" onClick={() => setSelectedProp(null)}><X size={24} /></button>
              </div>
              
              {selectedProp.raw_data?.images?.[0] && (
                <img src={selectedProp.raw_data.images[0]} alt="Property" className="property-image" />
              )}
              
              <div className="score-badge">
                {Math.round(selectedProp.score || 0)} / 100
              </div>
              
              <div className="metric-grid">
                <div className="metric-card">
                  <span className="metric-label">Price</span>
                  <span className="metric-value">£{selectedProp.price_pcm} pcm</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Bedrooms</span>
                  <span className="metric-value">{selectedProp.bedrooms}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Total Size</span>
                  <span className="metric-value">{selectedProp.raw_data?.sqft || '?'} sqft</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Type</span>
                  <span className="metric-value">{selectedProp.property_type || 'Unknown'}</span>
                </div>
              </div>
              
              <div className="pros-cons">
                {selectedProp.breakdown?.pros?.map((pro, i) => (
                  <div key={i} className="pro-item">
                    <CheckCircle2 size={18} />
                    <span>{pro}</span>
                  </div>
                ))}
                {selectedProp.breakdown?.cons?.map((con, i) => (
                  <div key={i} className="con-item">
                    <XCircle size={18} />
                    <span>{con}</span>
                  </div>
                ))}
              </div>
              
              {selectedProp.raw_data?.url && (
                <a href={selectedProp.raw_data.url} target="_blank" rel="noopener noreferrer" className="link-button">
                  View on Rightmove
                </a>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
