import { useState, useEffect, useMemo } from 'react';
import { collection, getDocs, doc, updateDoc } from 'firebase/firestore';
import { MapContainer, TileLayer, Marker, Tooltip } from 'react-leaflet';
import L from 'leaflet';
import { db } from './firebase';
import { X, CheckCircle2, XCircle, Map as MapIcon, ChevronLeft, ChevronRight, Pin, Search, List } from 'lucide-react';
import Slider from 'rc-slider';
import 'rc-slider/assets/index.css';
import pois from './pois.json';

interface Property {
  id: string;
  score: number;
  ignored: boolean;
  price_pcm?: number;
  bedrooms?: number;
  latitude?: number;
  longitude?: number;
  property_type?: string;
  sqft?: number;
  has_garden?: boolean;
  has_lift?: boolean;
  reception_on_ground_floor?: boolean;
  description?: string;
  listing_update?: string;
  pinned?: boolean;
  user_note?: string;
  user_status?: string;
  price_per_sqft?: number;
  commute_mins?: number;
  breakdown?: {
    pros?: string[];
    cons?: string[];
    scorecard?: Record<string, number>;
  };
  raw_data?: any;
}

const NoteEditor = ({ propId, initialNote, onSave }: { propId: string, initialNote: string, onSave: (id: string, note: string) => void }) => {
  const [note, setNote] = useState(initialNote);
  
  useEffect(() => { setNote(initialNote); }, [propId, initialNote]);

  return (
    <textarea 
      className="note-textarea"
      placeholder="Add private notes here..."
      value={note}
      onChange={(e) => setNote(e.target.value)}
      onBlur={() => { if (note !== initialNote) onSave(propId, note); }}
    />
  );
};

const getScoreColor = (score: number) => {
  const clampedScore = Math.max(0, Math.min(100, score));
  // Use a quadratic curve to make it harder to reach green hues.
  // This makes 50 ~ hue 30 (orange), 70 ~ hue 58 (yellow), 100 ~ hue 120 (green).
  const normalized = clampedScore / 100;
  const hue = Math.pow(normalized, 2) * 120;
  return `hsl(${hue}, 80%, 45%)`;
};

const createCustomIcon = (score: number, ignored: boolean, pinned: boolean = false, fresh: boolean = false) => {
  const bgColor = ignored ? 'var(--danger)' : getScoreColor(score);
  return L.divIcon({
    className: `custom-marker ${ignored ? 'ignored' : ''} ${pinned ? 'pinned' : ''} ${fresh ? 'fresh' : ''}`,
    html: `<div class="custom-marker-inner" style="background-color: ${bgColor};">${pinned ? '⭐' : Math.round(score)}</div>`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
  });
};

// Helper to normalize Rightmove date strings to ISO 8601 (YYYY-MM-DD)
const normalizeToISO8601 = (dateStr?: string) => {
  if (!dateStr) return undefined;
  
  const lower = dateStr.toLowerCase();
  if (lower.includes('today')) return new Date().toISOString().split('T')[0];
  if (lower.includes('yesterday')) {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().split('T')[0];
  }

  const ukDateMatch = dateStr.match(/(\d{2})\/(\d{2})\/(\d{4})/);
  let parsedDate = new Date(dateStr);
  if (ukDateMatch) {
    parsedDate = new Date(`${ukDateMatch[3]}-${ukDateMatch[2]}-${ukDateMatch[1]}T00:00:00Z`);
  }

  if (!isNaN(parsedDate.getTime())) {
    return parsedDate.toISOString().split('T')[0];
  }
  
  return dateStr; // fallback
};

const isFresh = (isoDateStr?: string) => {
  if (!isoDateStr) return false;
  const date = new Date(isoDateStr);
  if (isNaN(date.getTime())) return false;
  const now = new Date();
  const diffTime = Math.abs(now.getTime() - date.getTime());
  const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
  return diffDays <= 3;
};

const createPoiIcon = (type: string) => {
  return L.divIcon({
    className: `custom-marker poi-marker ${type}`,
    html: `<div class="custom-marker-inner">📍</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });
};

const PRICE_MARKS = [
  0, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000, 
  2250, 2500, 2750, 3000, 3250, 3500, 4000, 4500, 5000, 6000, 7000, 8000, 9000, 10000, 12500, 15000, 20000, 30000, 50000, 75000, 99999
];

const priceToSliderIndex = (price: number) => {
  let closestIdx = 0;
  let minDiff = Infinity;
  for (let i = 0; i < PRICE_MARKS.length; i++) {
    const diff = Math.abs(PRICE_MARKS[i] - price);
    if (diff < minDiff) {
      minDiff = diff;
      closestIdx = i;
    }
  }
  return closestIdx;
};

function useLocalStorageState<T>(key: string, defaultValue: T): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [state, setState] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored !== null ? JSON.parse(stored) : defaultValue;
    } catch (e) {
      return defaultValue;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch (e) {
      console.warn("Could not save to localStorage", e);
    }
  }, [key, state]);

  return [state, setState];
}

function App() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Dynamic Scoring Weights
  const [showWeightsPanel, setShowWeightsPanel] = useLocalStorageState('pinger_showWeightsPanel', false);
  const [weights, setWeights] = useLocalStorageState('pinger_weights', {
    price: 15,
    commute: 15,
    natural_light: 20,
    period_features: 15,
    sash_windows: 5,
    garden: 10,
    high_ceilings: 15
  });

  // Filters
  const [minScore, setMinScore] = useLocalStorageState('pinger_minScore', 50);
  const [priceRange, setPriceRange] = useLocalStorageState<number[]>('pinger_priceRange', [0, 99999]);
  const [minBeds, setMinBeds] = useLocalStorageState('pinger_minBeds', 1);
  const [minSqft, setMinSqft] = useLocalStorageState('pinger_minSqft', 0);
  const [requireGarden, setRequireGarden] = useLocalStorageState('pinger_requireGarden', false);
  const [requireLift, setRequireLift] = useLocalStorageState('pinger_requireLift', false);
  const [disabledTypes, setDisabledTypes] = useLocalStorageState<string[]>('pinger_disabledTypes', []);
  const [keywordFilter, setKeywordFilter] = useLocalStorageState('pinger_keywordFilter', '');
  const [showPinnedPanel, setShowPinnedPanel] = useLocalStorageState('pinger_showPinnedPanel', false);
  const [maxPricePerSqft, setMaxPricePerSqft] = useLocalStorageState('pinger_maxPricePerSqft', 0);
  const [maxCommuteMins, setMaxCommuteMins] = useLocalStorageState('pinger_maxCommuteMins', 0);
  
  // Selected Property
  const [selectedProp, setSelectedProp] = useState<Property | null>(null);
  const [currentImageIndex, setCurrentImageIndex] = useState(0);

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
          const desc = data.description || data.raw_data?.description || "";
          
          let sqft = data.sqft || data.raw_data?.sqft;
          if (!sqft) {
            const sqftMatch = desc.match(/(\d[,.\d]*)\s*(sq\s*ft|square\s*feet|sqft)/i);
            if (sqftMatch) {
              sqft = parseInt(sqftMatch[1].replace(/,/g, ''), 10);
            }
          }

          const has_garden = data.has_garden || data.raw_data?.has_garden;
          const has_lift = data.has_lift || data.raw_data?.has_lift || 
                           desc.toLowerCase().includes('lift') || 
                           desc.toLowerCase().includes('elevator');
                           
          const reception_on_ground_floor = data.reception_on_ground_floor ?? data.raw_data?.reception_on_ground_floor;
                           
          const listing_update = normalizeToISO8601(data.listing_update || data.raw_data?.listing_update);

          if (lat && lng) {
            let pps: number | undefined;
            if (price && sqft && sqft > 0) {
              pps = Math.round((price / sqft) * 10) / 10;
            }

            props.push({ 
              ...data, 
              id: doc.id,
              latitude: lat,
              longitude: lng,
              price_pcm: price,
              bedrooms: beds,
              property_type: type,
              sqft: sqft,
              has_garden: has_garden,
              has_lift: has_lift,
              reception_on_ground_floor: reception_on_ground_floor,
              listing_update: listing_update,
              pinned: data.pinned || false,
              user_note: data.user_note || '',
              user_status: data.user_status || 'None',
              price_per_sqft: pps,
              commute_mins: data.commute_mins
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

  const scoredProperties = useMemo(() => {
    return properties.map(p => {
      // If it doesn't have a score or scorecard, return as is
      if (typeof p.score !== 'number' || !p.breakdown?.scorecard) return p;
      
      let dynamicScore = 0;
      const sc = p.breakdown.scorecard;
      
      // Default backend weights
      const DW = { price: 15, commute: 15, natural_light: 20, period_features: 15, sash_windows: 5, garden: 10, high_ceilings: 15 };
      
      // Scale each positive score component
      if (sc.price !== undefined) dynamicScore += (sc.price / Math.max(1, DW.price)) * weights.price;
      // Commute score can be negative, scaling applies the same
      if (sc.commute !== undefined) dynamicScore += (sc.commute / Math.max(1, DW.commute)) * weights.commute;
      if (sc.natural_light !== undefined) dynamicScore += (sc.natural_light / Math.max(1, DW.natural_light)) * weights.natural_light;
      if (sc.period_features !== undefined) dynamicScore += DW.period_features > 0 ? (sc.period_features / DW.period_features) * weights.period_features : 0;
      if (sc.sash_windows !== undefined) dynamicScore += DW.sash_windows > 0 ? (sc.sash_windows / DW.sash_windows) * weights.sash_windows : 0;
      if (sc.garden !== undefined) dynamicScore += DW.garden > 0 ? (sc.garden / DW.garden) * weights.garden : 0;
      if (sc.high_ceilings !== undefined) dynamicScore += DW.high_ceilings > 0 ? (sc.high_ceilings / DW.high_ceilings) * weights.high_ceilings : 0;
      
      // Add all penalties directly (they start with 'penalty_')
      Object.keys(sc).forEach(k => {
        if (k.startsWith('penalty_')) {
          dynamicScore += sc[k];
        }
      });
      
      // Ensure score doesn't go below 0 or above 100 for display
      const finalScore = Math.max(0, Math.min(100, Math.round(dynamicScore * 10) / 10));
      return { ...p, score: finalScore };
    });
  }, [properties, weights]);

  const filteredProperties = useMemo(() => {
    return scoredProperties.filter(p => {
      if (p.ignored && typeof p.score !== 'number') return false; // Hide hard dealbreaker failures
      if (typeof p.score === 'number' && p.score < minScore) return false;
      if (p.price_pcm && (p.price_pcm < priceRange[0] || p.price_pcm > priceRange[1])) return false;
      if (p.bedrooms && p.bedrooms < minBeds) return false;
      if (minSqft > 0 && p.sqft && p.sqft < minSqft) return false;
      if (requireGarden && !p.has_garden) return false;
      if (requireLift && !p.has_lift && !p.reception_on_ground_floor) return false;
      if (disabledTypes.includes(p.property_type || 'Unknown')) return false;
      if (maxPricePerSqft > 0 && p.price_per_sqft && p.price_per_sqft > maxPricePerSqft) return false;
      if (maxCommuteMins > 0 && p.commute_mins && p.commute_mins > maxCommuteMins) return false;

      if (keywordFilter.trim() !== '') {
        const query = keywordFilter.toLowerCase();
        const desc = (p.description || p.raw_data?.description || '').toLowerCase();
        const pros = p.breakdown?.pros?.join(' ').toLowerCase() || '';
        const cons = p.breakdown?.cons?.join(' ').toLowerCase() || '';
        if (!desc.includes(query) && !pros.includes(query) && !cons.includes(query)) {
          return false;
        }
      }

      return true;
    });
  }, [scoredProperties, minScore, priceRange, minBeds, minSqft, requireGarden, requireLift, disabledTypes, keywordFilter, maxPricePerSqft, maxCommuteMins]);

  const uniqueTypes = useMemo(() => {
    return Array.from(new Set(properties.map(p => p.property_type || 'Unknown'))).sort();
  }, [properties]);

  const getImages = (p: any) => {
    if (!p) return [];
    if (p.images && p.images.length > 0 && typeof p.images[0] === 'string') return p.images;
    if (p.images && p.images.length > 0 && p.images[0].url) return p.images.map((img: any) => img.url);
    if (p.raw_data?.images && p.raw_data.images.length > 0) {
      if (typeof p.raw_data.images[0] === 'string') return p.raw_data.images;
      if (p.raw_data.images[0].url) return p.raw_data.images.map((img: any) => img.url);
    }
    return ['https://images.unsplash.com/photo-1560518883-ce09059eeffa?w=800&q=80'];
  };

  const images = getImages(selectedProp);

  const togglePin = async (p: Property) => {
    const newPinnedStatus = !p.pinned;
    
    // Optimistic UI update
    setProperties(prev => prev.map(prop => 
      prop.id === p.id ? { ...prop, pinned: newPinnedStatus } : prop
    ));
    if (selectedProp && selectedProp.id === p.id) {
      setSelectedProp({ ...selectedProp, pinned: newPinnedStatus });
    }

    try {
      const propRef = doc(db, 'properties', p.id);
      await updateDoc(propRef, { pinned: newPinnedStatus });
    } catch (e: any) {
      console.error("Error updating pin status:", e);
      // Revert optimistic update
      setProperties(prev => prev.map(prop => 
        prop.id === p.id ? { ...prop, pinned: !newPinnedStatus } : prop
      ));
      if (selectedProp && selectedProp.id === p.id) {
        setSelectedProp({ ...selectedProp, pinned: !newPinnedStatus });
      }
      alert("Failed to save to database. You need to update your Firestore Security Rules in the Firebase Console to allow write access.");
    }
  };

  const updatePropertyDetails = async (id: string, updates: Partial<Property>) => {
    // Optimistic UI update
    setProperties(prev => prev.map(prop => 
      prop.id === id ? { ...prop, ...updates } : prop
    ));
    if (selectedProp && selectedProp.id === id) {
      setSelectedProp(prev => prev ? { ...prev, ...updates } : null);
    }

    try {
      const propRef = doc(db, 'properties', id);
      await updateDoc(propRef, updates);
    } catch (e: any) {
      console.error("Error updating property:", e);
      alert("Failed to save. Check Firestore permissions.");
    }
  };

  return (
    <div className="dashboard-layout">
      {/* Sidebar */}
      <div className="sidebar glass">
        <h1><MapIcon size={28} /> Property Pinger</h1>
        
        <div style={{ marginTop: '20px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <button className="toggle-pinned-btn" onClick={() => setShowPinnedPanel(true)}>
            <List size={20} /> View Pinned Properties
          </button>

          <div className="filter-group">
            <div style={{ position: 'relative' }}>
              <input 
                type="text" 
                className="search-input" 
                placeholder="Search keywords (e.g. balcony, garage)" 
                value={keywordFilter}
                onChange={e => setKeywordFilter(e.target.value)}
                style={{ paddingLeft: '36px' }}
              />
              <Search size={18} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)' }} />
            </div>
          </div>

          <div className="filter-group">
            <div style={{ display: 'flex', justifyContent: 'space-between', cursor: 'pointer', marginBottom: showWeightsPanel ? '16px' : '0' }} onClick={() => setShowWeightsPanel(!showWeightsPanel)}>
              <label style={{ cursor: 'pointer', margin: 0 }}>⚙️ Dynamic Scoring Weights</label>
              <span>{showWeightsPanel ? '▼' : '▶'}</span>
            </div>
            
            {showWeightsPanel && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', padding: '12px', backgroundColor: 'var(--surface-light)', borderRadius: '8px' }}>
                {Object.entries(weights).map(([key, val]) => (
                  <div key={key}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '4px' }}>
                      <span style={{ textTransform: 'capitalize' }}>{key.replace('_', ' ')}</span>
                      <span>{val}</span>
                    </div>
                    <input 
                      type="range" min="0" max="40" step="1" value={val} 
                      onChange={e => setWeights({...weights, [key]: Number(e.target.value)})} 
                      style={{ width: '100%' }}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="filter-group">
            <label>Minimum Score</label>
            <input 
              type="range" min="0" max="100" value={minScore} 
              onChange={e => setMinScore(Number(e.target.value))} 
            />
            <div className="value-display">{minScore} pts</div>
          </div>
          
          <div className="filter-group">
            <label>Price Range</label>
            <div style={{ padding: '0 8px', marginTop: '8px', marginBottom: '12px' }}>
              <Slider 
                range 
                min={0} 
                max={PRICE_MARKS.length - 1} 
                step={1} 
                value={[priceToSliderIndex(priceRange[0]), priceToSliderIndex(priceRange[1])]} 
                onChange={(val) => {
                  const indices = val as number[];
                  setPriceRange([PRICE_MARKS[indices[0]], PRICE_MARKS[indices[1]]]);
                }} 
                styles={{
                  track: { backgroundColor: 'var(--accent-color)' },
                  handle: { borderColor: 'var(--accent-color)', backgroundColor: '#fff', opacity: 1 }
                }}
              />
            </div>
            <div className="value-display">£{priceRange[0]} - £{priceRange[1]} pcm</div>
          </div>
          
          <div className="filter-group">
            <label>Minimum Beds</label>
            <input 
              type="range" min="1" max="5" value={minBeds} 
              onChange={e => setMinBeds(Number(e.target.value))} 
            />
            <div className="value-display">{minBeds}+ Beds</div>
          </div>

          <div className="filter-group">
            <label>Minimum Floor Area</label>
            <input 
              type="range" min="0" max="2000" step="50" value={minSqft} 
              onChange={e => setMinSqft(Number(e.target.value))} 
            />
            <div className="value-display">{minSqft === 0 ? 'Any' : `${minSqft}+ sqft`}</div>
          </div>
          
          <div className="filter-group">
            <label>Maximum Price/Sqft</label>
            <input 
              type="range" min="0" max="15" step="0.5" value={maxPricePerSqft} 
              onChange={e => setMaxPricePerSqft(Number(e.target.value))} 
            />
            <div className="value-display">{maxPricePerSqft === 0 ? 'Any' : `£${maxPricePerSqft}/sqft`}</div>
          </div>

          <div className="filter-group">
            <label>Maximum Commute Time</label>
            <input 
              type="range" min="0" max="120" step="5" value={maxCommuteMins} 
              onChange={e => setMaxCommuteMins(Number(e.target.value))} 
            />
            <div className="value-display">{maxCommuteMins === 0 ? 'Any' : `${maxCommuteMins} mins`}</div>
          </div>

          <div className="filter-group">
            <label>Property Type</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
              {uniqueTypes.map(type => (
                <label key={type} className="checkbox-label" style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '0.9rem', color: '#cbd5e1' }}>
                  <input 
                    type="checkbox" 
                    checked={!disabledTypes.includes(type)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setDisabledTypes(disabledTypes.filter(t => t !== type));
                      } else {
                        setDisabledTypes([...disabledTypes, type]);
                      }
                    }} 
                    style={{ accentColor: 'var(--accent)' }}
                  />
                  <span style={{ textTransform: 'capitalize' }}>{type}</span>
                </label>
              ))}
            </div>
          </div>
          
          <div className="toggle-group" onClick={() => setRequireGarden(!requireGarden)}>
            <div className={`toggle-switch ${requireGarden ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Require Garden</label>
          </div>

          <div className="toggle-group" onClick={() => setRequireLift(!requireLift)}>
            <div className={`toggle-switch ${requireLift ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Require Lift</label>
          </div>
          

        </div>
        
        <div style={{ marginTop: 'auto', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
          Showing {filteredProperties.length} of {properties.length} properties
        </div>
      </div>

      {/* Pinned Panel */}
      <div className={`pinned-panel ${showPinnedPanel ? 'open' : ''}`}>
        <div className="pinned-panel-header">
          <h2>Pinned Properties</h2>
          <button className="close-btn" onClick={() => setShowPinnedPanel(false)}>
            <X size={24} />
          </button>
        </div>
        <div className="pinned-list">
          {properties.filter(p => p.pinned).length === 0 ? (
            <div style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '40px 20px' }}>
              No pinned properties yet. Click a property and tap the star icon to pin it.
            </div>
          ) : (
            properties.filter(p => p.pinned).map(p => (
              <div key={p.id} className="pinned-card" onClick={() => { setSelectedProp(p); setCurrentImageIndex(0); }}>
                <img 
                  src={getImages(p)[0]} 
                  alt="Property" 
                  className="pinned-card-image"
                />
                <div className="pinned-card-content">
                  <div className="pinned-card-header">
                    <div className="pinned-card-price">£{p.price_pcm} pcm</div>
                    <div className="pinned-card-badges">
                      {p.user_status && p.user_status !== 'None' && (
                        <span className="status-badge">{p.user_status}</span>
                      )}
                      <div className="pinned-card-score">{Math.round(p.score || 0)}</div>
                    </div>
                  </div>
                  <div className="pinned-card-details">
                    <span>{p.bedrooms} Beds</span>
                    <span>•</span>
                    <span>{p.property_type || 'Unknown'}</span>
                    {p.sqft && (
                      <>
                        <span>•</span>
                        <span>{p.sqft} sqft</span>
                      </>
                    )}
                  </div>
                  <div className="pinned-card-actions">
                    <button 
                      className="text-btn danger" 
                      onClick={(e) => { e.stopPropagation(); togglePin(p); }}
                    >
                      Unpin
                    </button>
                    {p.raw_data?.url && (
                      <a 
                        href={p.raw_data.url} 
                        target="_blank" 
                        rel="noopener noreferrer" 
                        className="text-btn"
                        onClick={(e) => e.stopPropagation()}
                      >
                        Rightmove
                      </a>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
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
              url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
            />
            {filteredProperties.map(p => (
              <Marker 
                key={p.id} 
                position={[p.latitude!, p.longitude!]}
                icon={createCustomIcon(p.score || 0, p.ignored, p.pinned || false, isFresh(p.listing_update))}
                zIndexOffset={Math.round((p.score || 0) * 1000) + (p.pinned ? 100000 : 0)}
                riseOnHover={true}
                eventHandlers={{
                  click: () => { setSelectedProp(p); setCurrentImageIndex(0); },
                }}
              />
            ))}
            {pois.map((poi: any, index: number) => (
              <Marker
                key={`poi-${index}`}
                position={[poi.lat, poi.lng]}
                icon={createPoiIcon(poi.type)}
              >
                <Tooltip direction="top" offset={[0, -12]} opacity={1}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem', color: '#1e293b' }}>
                    {poi.name}
                  </div>
                </Tooltip>
              </Marker>
            ))}
          </MapContainer>
        )}
        
        {/* Property Drawer */}
        <div className={`property-drawer glass ${selectedProp ? 'open' : ''}`}>
          {selectedProp && (
            <>
              <div className="drawer-header">
                {isFresh(selectedProp.listing_update) && <div className="fresh-badge">NEW</div>}
                <div className="drawer-hero-container">
                  <img 
                    src={images[currentImageIndex]} 
                    alt={selectedProp.raw_data?.display_address || 'Property'} 
                    className="drawer-hero-image"
                  />
                  {images.length > 1 && (
                    <>
                      <button 
                        className="carousel-btn left" 
                        onClick={() => setCurrentImageIndex((prev) => (prev > 0 ? prev - 1 : images.length - 1))}
                      >
                        <ChevronLeft size={20} />
                      </button>
                      <button 
                        className="carousel-btn right" 
                        onClick={() => setCurrentImageIndex((prev) => (prev < images.length - 1 ? prev + 1 : 0))}
                      >
                        <ChevronRight size={20} />
                      </button>
                      <div className="carousel-indicators">
                        {images.map((_: any, i: number) => (
                          <div key={i} className={`carousel-indicator ${i === currentImageIndex ? 'active' : ''}`} />
                        ))}
                      </div>
                    </>
                  )}
                </div>
                <button 
                  className={`drawer-action-btn ${selectedProp.pinned ? 'pinned' : ''}`} 
                  onClick={() => togglePin(selectedProp)}
                  style={{ right: '56px' }}
                >
                  <Pin size={18} fill={selectedProp.pinned ? 'currentColor' : 'none'} />
                </button>
                <button 
                  className="drawer-action-btn" 
                  onClick={() => setSelectedProp(null)}
                  style={{ right: '12px' }}
                >
                  <X size={20} />
                </button>
              </div>
              
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
                  <span className="metric-value">{selectedProp.sqft ? `${selectedProp.sqft} sqft` : ''}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Type</span>
                  <span className="metric-value">{selectedProp.property_type || 'Unknown'}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Commute</span>
                  <span className="metric-value">{selectedProp.commute_mins ? `${Math.ceil(selectedProp.commute_mins)}m` : 'N/A'}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">£/sqft</span>
                  <span className="metric-value">{selectedProp.price_per_sqft ? `£${selectedProp.price_per_sqft}` : 'N/A'}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Listed</span>
                  <span className="metric-value">{selectedProp.listing_update || 'Unknown'}</span>
                </div>
              </div>
              
              <div className="workflow-section">
                <label>Status</label>
                <select 
                  className="status-select"
                  value={selectedProp.user_status || 'None'}
                  onChange={(e) => updatePropertyDetails(selectedProp.id, { user_status: e.target.value })}
                >
                  <option value="None">None</option>
                  <option value="Interested">Interested</option>
                  <option value="Contacted">Contacted Agent</option>
                  <option value="Viewing">Viewing Booked</option>
                  <option value="Offer">Made Offer</option>
                  <option value="Rejected">Rejected</option>
                </select>

                <label>Private Notes</label>
                <NoteEditor 
                  propId={selectedProp.id} 
                  initialNote={selectedProp.user_note || ''} 
                  onSave={(id, note) => updatePropertyDetails(id, { user_note: note })} 
                />
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
