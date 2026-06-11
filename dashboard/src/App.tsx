import { useState, useEffect, useMemo, useRef } from 'react';
import { collection, getDocs, doc, updateDoc } from 'firebase/firestore';
import { MapContainer, TileLayer, Marker, Tooltip, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import { db } from './firebase';
import { X, CheckCircle2, XCircle, Map as MapIcon, ChevronLeft, ChevronRight, Pin, Search, List, Globe, Clock, MapPin, ExternalLink, MessageSquare, Settings, ChevronDown, ChevronUp, Trash2 } from 'lucide-react';
import Slider from 'rc-slider';
import 'rc-slider/assets/index.css';
import pois from './pois.json';

interface RoomNode {
  name: string;
  length_m: number;
  width_m: number;
  room_type: string;
  windows?: { orientation: string }[];
  sunlight_hours?: number;
}

interface DoorEdge {
  from_room: string;
  to_room: string;
  width_m: number;
}

interface FloorplanGraph {
  rooms: RoomNode[];
  doors: DoorEdge[];
  entrance_room?: string;
}

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
  has_ac?: boolean;
  has_underfloor_heating?: boolean;
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
  floorplan_graph?: FloorplanGraph;
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

const getScoreColor = (score: number, viewed: boolean = false) => {
  const clampedScore = Math.max(0, Math.min(100, score));
  // Use a quadratic curve to make it harder to reach green hues.
  // This makes 50 ~ hue 30 (orange), 70 ~ hue 58 (yellow), 100 ~ hue 120 (green).
  const normalized = clampedScore / 100;
  const hue = Math.pow(normalized, 2) * 120;
  return `hsla(${hue}, 85%, 50%, ${viewed ? 0.4 : 1})`;
};

const createCustomIcon = (score: number, ignored: boolean, pinned: boolean = false, fresh: boolean = false, viewed: boolean = false, isHovered: boolean = false) => {
  const bgColor = pinned ? '#ff4f70' : (ignored ? 'var(--danger)' : getScoreColor(score, viewed));
  const size = pinned && !isHovered ? 24 : (isHovered && pinned ? 34 : 36);
  const fontSize = pinned ? size * 0.6 : 14;

  const anchorY = pinned ? size / 2 : size * 1.207;
  const heartSvg = `<svg viewBox="0 0 24 24" fill="white" width="65%" height="65%" style="margin-top: 1px;"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>`;

  return L.divIcon({
    className: `custom-marker ${ignored ? 'ignored' : ''} ${pinned ? 'pinned' : ''} ${fresh ? 'fresh' : ''} ${viewed && !pinned ? 'viewed' : ''} ${isHovered ? 'hovered' : ''}`,
    html: `<div class="custom-marker-inner" style="background-color: ${bgColor}; width: ${size}px; height: ${size}px;">
             <span class="custom-marker-content" style="font-size: ${fontSize}px; ${pinned ? 'width: 100%; height: 100%;' : ''}">${pinned ? heartSvg : Math.round(score)}</span>
           </div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, anchorY],
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

const validateFit = (graph: FloorplanGraph | undefined, itemLength: number, itemWidth: number) => {
  if (!graph || !graph.rooms || graph.rooms.length === 0) return { fits: false, reason: "No floorplan graph available." };

  const entrance = graph.entrance_room || graph.rooms[0].name;
  const startNode = graph.rooms.find(r => r.name === entrance);
  if (!startNode) return { fits: false, reason: "Entrance not found in floorplan." };

  const minItemDim = Math.min(itemLength, itemWidth);
  const maxItemDim = Math.max(itemLength, itemWidth);
  const itemArea = itemLength * itemWidth;

  const reachable = new Set<string>();
  const queue = [entrance];
  
  const adjList = new Map<string, Array<{to: string, width: number}>>();
  graph.rooms.forEach(r => adjList.set(r.name, []));
  
  graph.doors.forEach(d => {
    if (adjList.has(d.from_room)) {
      adjList.get(d.from_room)!.push({ to: d.to_room, width: d.width_m });
    }
    if (adjList.has(d.to_room)) {
      adjList.get(d.to_room)!.push({ to: d.from_room, width: d.width_m });
    }
  });

  while (queue.length > 0) {
    const curr = queue.shift()!;
    if (reachable.has(curr)) continue;
    reachable.add(curr);

    const neighbors = adjList.get(curr) || [];
    for (const edge of neighbors) {
      if (edge.width >= minItemDim && !reachable.has(edge.to)) {
        queue.push(edge.to);
      }
    }
  }

  const reception = graph.rooms.find(r => r.room_type === 'reception');
  if (!reception) return { fits: false, reason: "No reception room identified.", reachableRooms: Array.from(reachable) };

  if (!reachable.has(reception.name)) {
    return { fits: false, reason: `Reception room (${reception.name}) is unreachable. A door or hallway is too narrow for ${minItemDim}m.`, reachableRooms: Array.from(reachable) };
  }

  const roomMinDim = Math.min(reception.length_m, reception.width_m);
  const roomMaxDim = Math.max(reception.length_m, reception.width_m);
  const roomArea = reception.length_m * reception.width_m;

  if (roomMinDim < minItemDim || roomMaxDim < maxItemDim) {
    return { fits: false, reason: `Reception room (${reception.name}) dimensions (${reception.length_m}x${reception.width_m}m) are too small.`, reachableRooms: Array.from(reachable) };
  }
  
  if (roomArea < itemArea * 3) {
      return { fits: false, reason: `Reception room (${reception.name}) does not have enough free space/wall space for this item.`, reachableRooms: Array.from(reachable)};
  }

  return { 
    fits: true, 
    reason: `Item fits! Path from entrance to ${reception.name} is clear.`,
    reachableRooms: Array.from(reachable)
  };
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

const formatListedDate = (dateStr?: string) => {
  if (!dateStr || dateStr === 'Unknown') return 'Unknown';
  
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return dateStr;

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const listedDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  
  const diffTime = today.getTime() - listedDay.getTime();
  const diffDays = Math.round(diffTime / (1000 * 60 * 60 * 24));
  
  if (diffDays === 0) return `${dateStr} (Today)`;
  if (diffDays === 1) return `${dateStr} (Yesterday)`;
  if (diffDays < 0) return dateStr; // sanity check
  
  if (diffDays > 30) {
    const months = Math.floor(diffDays / 30);
    return `${dateStr} (> ${months} month${months > 1 ? 's' : ''} ago)`;
  }
  
  return `${dateStr} (${diffDays} days ago)`;
};

const createPoiIcon = (type: string) => {
  const size = 24;
  const pinSvg = `<svg viewBox="0 0 24 24" fill="white" width="60%" height="60%"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>`;
  
  return L.divIcon({
    className: `custom-marker poi-marker ${type}`,
    html: `<div class="custom-marker-inner" style="width: ${size}px; height: ${size}px;">
             <span class="custom-marker-content" style="width: 100%; height: 100%;">${pinSvg}</span>
           </div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
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

const StarRating = ({ score100 }: { score100: number }) => {
  const score5 = (Math.max(0, Math.min(100, score100)) / 100) * 5;
  const stars = [];
  for (let i = 1; i <= 5; i++) {
    let fillPct = 0;
    if (score5 >= i) fillPct = 100;
    else if (score5 > i - 1) fillPct = (score5 - (i - 1)) * 100;
    
    stars.push(
      <div key={i} style={{ position: 'relative', display: 'inline-block', width: '14px', height: '14px', marginRight: '2px' }}>
        <svg viewBox="0 0 24 24" fill="#e8eaed" width="100%" height="100%" style={{ position: 'absolute', top: 0, left: 0 }}>
          <path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z" />
        </svg>
        <svg viewBox="0 0 24 24" fill="#fbbc04" width="100%" height="100%" style={{ position: 'absolute', top: 0, left: 0, clipPath: `inset(0 ${100 - fillPct}% 0 0)` }}>
          <path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z" />
        </svg>
      </div>
    );
  }
  return <div style={{ display: 'flex', alignItems: 'center' }}>{stars}</div>;
};

const getScoreContext = (key: string, prop: any, w: any, p: any, priceRange: number[]) => {
  switch(key) {
    case 'total_size':
      return `Measured Size: ${prop.sqft ? prop.sqft + ' sqft' : 'Unknown'}. Target: ${p.optimal_total_sqft} sqft. Max Weight: ${w.total_size}.`;
    case 'price':
      return `Price: £${prop.price_pcm} pcm. Max Budget: ${priceRange[1] > 90000 ? 'No Limit' : '£' + priceRange[1]}. Max Weight: ${w.price}.`;
    case 'commute':
      return `Measured Commute: ${prop.commute_mins ? Math.ceil(prop.commute_mins) + ' mins' : 'Unknown'}. Max Weight: ${w.commute}.`;
    case 'bedroom_size':
      return `Target: ${p.bedroom_optimal_area_sqm} sqm. Max Weight: ${w.bedroom_size}.`;
    case 'natural_light':
      return `Estimated based on orientation/windows. Max Weight: ${w.natural_light}.`;
    case 'period_features':
      return `Detected from description. Max Weight: ${w.period_features}.`;
    case 'sash_windows':
      return `Detected from description. Max Weight: ${w.sash_windows}.`;
    case 'garden':
      return `Detected from description. Max Weight: ${w.garden}.`;
    case 'high_ceilings':
      return `Target > ${p.high_ceiling_threshold_m}m. Max Weight: ${w.high_ceilings}.`;
    default:
      if (key.startsWith('penalty_')) {
        return `Penalty deduction applied based on listing analysis.`;
      }
      return '';
  }
};

const GoogleSelect = ({ label, value, options, onChange }: {
  label: string, 
  value: string | number, 
  options: {value: string | number, label: string}[], 
  onChange: (val: string | number) => void
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const selectedLabel = options.find(o => o.value === value)?.label || value;

  return (
    <div className={`g-select-container ${isOpen ? 'open' : ''}`} ref={containerRef}>
      <div className="g-select-button" onClick={() => setIsOpen(!isOpen)}>
        <div className="g-select-content">
          <div className="g-select-label">{label}</div>
          <div className="g-select-value">{selectedLabel}</div>
        </div>
        <div className="g-select-caret">
          {isOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </div>
      </div>
      {isOpen && (
        <div className="g-select-dropdown">
          {options.map(opt => (
            <div 
              key={opt.value} 
              className={`g-select-option ${opt.value === value ? 'selected' : ''}`}
              onClick={() => {
                onChange(opt.value);
                setIsOpen(false);
              }}
            >
              {opt.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

function MapEvents({ onClick }: { onClick: () => void }) {
  useMapEvents({
    click: () => {
      onClick();
    },
  });
  return null;
}

function App() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Dynamic Scoring Weights
  const [showSettingsPanel, setShowSettingsPanel] = useLocalStorageState('pinger_showSettingsPanel', false);
  const [weights, setWeights] = useLocalStorageState('pinger_weights', {
    price: 15,
    commute: 15,
    total_size: 10,
    natural_light: 20,
    period_features: 15,
    sash_windows: 5,
    garden: 10,
    high_ceilings: 15
  });

  const [penalties, setPenalties] = useLocalStorageState('pinger_penalties', {
    not_ground_floor_reception: -15,
    wide_angle_distortion: -10,
    poor_epc_rating: -30,
    noisy_location: -20
  });

  const [scoringParams, setScoringParams] = useLocalStorageState('pinger_scoringParams', {
    optimal_total_sqft: 1500,
    high_ceiling_threshold_m: 2.7,
    high_ceiling_max_scale_m: 3.3
  });


  // Filters
  const [minScore, setMinScore] = useLocalStorageState('pinger_minScore', 50);
  const [priceRange, setPriceRange] = useLocalStorageState<number[]>('pinger_priceRange', [0, 99999]);
  const [minBeds, setMinBeds] = useLocalStorageState('pinger_minBeds', 1);
  const [maxBeds, setMaxBeds] = useLocalStorageState('pinger_maxBeds', 5);
  const [minSqft, setMinSqft] = useLocalStorageState('pinger_minSqft', 0);
  const [requireGarden, setRequireGarden] = useLocalStorageState('pinger_requireGarden', false);
  const [requireLift, setRequireLift] = useLocalStorageState('pinger_requireLift', false);
  const [requireAC, setRequireAC] = useLocalStorageState('pinger_requireAC', false);
  const [excludeUnderfloorHeating, setExcludeUnderfloorHeating] = useLocalStorageState('pinger_excludeUnderfloorHeating', false);
  const [disabledTypes, setDisabledTypes] = useLocalStorageState<string[]>('pinger_disabledTypes', []);
  const [keywordFilter, setKeywordFilter] = useLocalStorageState('pinger_keywordFilter', '');
  const [showPinnedPanel, setShowPinnedPanel] = useLocalStorageState('pinger_showPinnedPanel', false);
  const [showItemPanel, setShowItemPanel] = useLocalStorageState('pinger_showItemPanel', false);
  const [maxPricePerSqft, setMaxPricePerSqft] = useLocalStorageState('pinger_maxPricePerSqft', 0);
  const [maxCommuteMins, setMaxCommuteMins] = useLocalStorageState('pinger_maxCommuteMins', 0);
  const [addedInLast, setAddedInLast] = useLocalStorageState('pinger_addedInLast', 0);
  const [showIgnored, setShowIgnored] = useLocalStorageState('pinger_showIgnored', false);
  const [viewedProperties, setViewedProperties] = useLocalStorageState<string[]>('pinger_viewedProperties', []);
  const [hideViewed, setHideViewed] = useLocalStorageState('pinger_hideViewed', false);
  const [statusFilter, setStatusFilter] = useLocalStorageState<string[]>('pinger_statusFilter', []);
  const [pinnedSortBy, setPinnedSortBy] = useLocalStorageState('pinger_pinnedSortBy', 'default');
  
  // Custom Fit Constraints
  const [customItems, setCustomItems] = useLocalStorageState('pinger_customItems', [
    { id: '1', name: 'Grand Piano', length: 2.0, width: 1.5 }
  ]);
  
  // Selected Property
  const [selectedProp, setSelectedProp] = useState<Property | null>(null);
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [hoveredPinnedId, setHoveredPinnedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'Overview' | 'Scorecard'>('Overview');
  const [isFloorplanExpanded, setIsFloorplanExpanded] = useState(false);

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
            const sqftMatch = desc.match(/([\d,.]+)\s*(sq\s*ft|square\s*feet|sqft)/i);
            const sqmMatch = desc.match(/([\d,.]+)\s*(sq\s*m|square\s*meters|square\s*metres|sqm|m2|m\^2|m²)/i);
            if (sqftMatch) {
              sqft = parseInt(sqftMatch[1].replace(/,/g, ''), 10);
            } else if (sqmMatch) {
              const sqm = parseFloat(sqmMatch[1].replace(/,/g, ''));
              sqft = Math.round(sqm * 10.7639);
            }
          }

          const has_garden = data.has_garden || data.raw_data?.has_garden;
          const has_lift = data.has_lift || data.raw_data?.has_lift || 
                           desc.toLowerCase().includes('lift') || 
                           desc.toLowerCase().includes('elevator');
                           
          const has_ac = data.has_ac ?? data.raw_data?.has_ac ?? 
                         desc.toLowerCase().match(/\b(air conditioning|air-conditioning|a\/c|ac|climate control|air-con|aircon)\b/) !== null;
                         
          const has_underfloor_heating = data.has_underfloor_heating ?? data.raw_data?.has_underfloor_heating ?? 
                         desc.toLowerCase().match(/\b(underfloor heating|under floor heating|under-floor heating|ufh|underfloor|radiant floor|heated floor)\b/) !== null;
                           
          const reception_on_ground_floor = data.reception_on_ground_floor ?? data.raw_data?.reception_on_ground_floor;
                           
          const listing_update = normalizeToISO8601(data.listing_update || data.raw_data?.listing_update);
          
          let commute_mins = data.commute_mins 
            ?? data.raw_data?.commute_mins 
            ?? data.raw_data?.commute_metrics_raw?.average_mins;
            
          if (commute_mins === undefined) {
            const commuteStr = [...(data.breakdown?.pros || []), ...(data.breakdown?.cons || [])].find(s => s.includes('Commute'));
            if (commuteStr) {
              const match = commuteStr.match(/(\d+)m/);
              if (match) commute_mins = parseInt(match[1], 10);
            }
          }
            
          if (commute_mins === 999) {
            commute_mins = undefined;
          }

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
              has_ac: has_ac,
              has_underfloor_heating: has_underfloor_heating,
              reception_on_ground_floor: reception_on_ground_floor,
              listing_update: listing_update,
              pinned: data.pinned || false,
              user_note: data.user_note || '',
              user_status: data.user_status || 'None',
              price_per_sqft: pps,
              commute_mins: commute_mins,
              floorplan_graph: data.floorplan_graph || data.raw_data?.floorplan_graph
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
      const newScorecard = { ...sc };
      
      // Default backend weights
      const DW = { price: 15, commute: 15, total_size: 10, natural_light: 20, period_features: 15, sash_windows: 5, garden: 10, high_ceilings: 15 };
      
      // Calculate total user weight to normalize the scores out of 100
      const safeWeights = {
        price: 15, // Fixed price weight
        commute: weights.commute ?? DW.commute,
        total_size: weights.total_size ?? DW.total_size,
        natural_light: weights.natural_light ?? DW.natural_light,
        period_features: weights.period_features ?? DW.period_features,
        sash_windows: weights.sash_windows ?? DW.sash_windows,
        garden: weights.garden ?? DW.garden,
        high_ceilings: weights.high_ceilings ?? DW.high_ceilings
      };
      const totalWeight = Object.values(safeWeights).reduce((sum, w) => sum + w, 0);
      
      if (totalWeight > 0) {
        // Total Size
        let sc_total_size = 0;
        if ((p.sqft || 0) > 0) {
          const size_ratio = Math.min(1.0, p.sqft! / scoringParams.optimal_total_sqft);
          sc_total_size = DW.total_size * size_ratio;
        }
        
        // High Ceilings
        let sc_high_ceilings = 0;
        const ceiling_height = p.raw_data?.max_ceiling_height_m || 0.0;
        if (ceiling_height > scoringParams.high_ceiling_threshold_m) {
            const scale_range = scoringParams.high_ceiling_max_scale_m - scoringParams.high_ceiling_threshold_m;
            const ratio = scale_range > 0 ? Math.min(1.0, (ceiling_height - scoringParams.high_ceiling_threshold_m) / scale_range) : 1.0;
            sc_high_ceilings = DW.high_ceilings * ratio;
        }

        // Normalize the score contributions to be out of 100 instead of unbounded
        if (sc.price !== undefined) {
          const val = (sc.price / Math.max(1, DW.price)) * (safeWeights.price / totalWeight) * 100;
          dynamicScore += val;
          newScorecard['price'] = Number(val.toFixed(1));
        }
        if (sc.commute !== undefined) {
          const val = (sc.commute / Math.max(1, DW.commute)) * (safeWeights.commute / totalWeight) * 100;
          dynamicScore += val;
          newScorecard['commute'] = Number(val.toFixed(1));
        }
        
        const norm_total_size = DW.total_size > 0 ? (sc_total_size / DW.total_size) * (safeWeights.total_size / totalWeight) * 100 : 0;
        dynamicScore += norm_total_size;
        newScorecard['total_size'] = Number(norm_total_size.toFixed(1));

        const norm_hc = DW.high_ceilings > 0 ? (sc_high_ceilings / DW.high_ceilings) * (safeWeights.high_ceilings / totalWeight) * 100 : 0;
        dynamicScore += norm_hc;
        newScorecard['high_ceilings'] = Number(norm_hc.toFixed(1));

        if (sc.natural_light !== undefined) {
          const val = (sc.natural_light / Math.max(1, DW.natural_light)) * (safeWeights.natural_light / totalWeight) * 100;
          dynamicScore += val;
          newScorecard['natural_light'] = Number(val.toFixed(1));
        }
        if (sc.period_features !== undefined) {
          const val = DW.period_features > 0 ? (sc.period_features / DW.period_features) * (safeWeights.period_features / totalWeight) * 100 : 0;
          dynamicScore += val;
          newScorecard['period_features'] = Number(val.toFixed(1));
        }
        if (sc.sash_windows !== undefined) {
          const val = DW.sash_windows > 0 ? (sc.sash_windows / DW.sash_windows) * (safeWeights.sash_windows / totalWeight) * 100 : 0;
          dynamicScore += val;
          newScorecard['sash_windows'] = Number(val.toFixed(1));
        }
        if (sc.garden !== undefined) {
          const val = DW.garden > 0 ? (sc.garden / DW.garden) * (safeWeights.garden / totalWeight) * 100 : 0;
          dynamicScore += val;
          newScorecard['garden'] = Number(val.toFixed(1));
        }
      }
      
      // Remove backend penalties to replace them dynamically
      Object.keys(newScorecard).forEach(k => {
        if (k.startsWith('penalty_')) {
          delete newScorecard[k];
        }
      });

      const epc = p.raw_data?.epc_rating;
      if (epc === 'F' || epc === 'G') {
        if (penalties.poor_epc_rating !== 0) {
          dynamicScore += penalties.poor_epc_rating;
          newScorecard['penalty_poor_epc'] = penalties.poor_epc_rating;
        }
      }

      const noisy = p.raw_data?.is_noisy_location ?? (p as any).is_noisy_location;
      if (noisy) {
        if (penalties.noisy_location !== 0) {
          dynamicScore += penalties.noisy_location;
          newScorecard['penalty_noisy_location'] = penalties.noisy_location;
        }
      }

      const recGround = p.raw_data?.reception_on_ground_floor ?? p.reception_on_ground_floor;
      if (recGround === false) {
        if (penalties.not_ground_floor_reception !== 0) {
          dynamicScore += penalties.not_ground_floor_reception;
          newScorecard['penalty_not_ground_floor_reception'] = penalties.not_ground_floor_reception;
        }
      }
      
      // Ensure score doesn't go below 0 or above 100 for display
      const finalScore = Math.max(0, Math.min(100, Math.round(dynamicScore * 10) / 10));
      return { 
        ...p, 
        score: finalScore,
        breakdown: {
          ...p.breakdown,
          scorecard: newScorecard
        }
      };
    });
  }, [properties, weights, scoringParams, penalties]);

  const filteredProperties = useMemo(() => {
    return scoredProperties.filter(p => {
      if (p.ignored) {
        return showIgnored; // Bypass all other filters if we explicitly want to see ignored properties
      }

      if (hideViewed && viewedProperties.includes(p.id)) return false;

      if (typeof p.score === 'number' && p.score < minScore) return false;
      if (p.price_pcm && (p.price_pcm < priceRange[0] || p.price_pcm > priceRange[1])) return false;
      if (p.bedrooms && (p.bedrooms < minBeds || p.bedrooms > maxBeds)) return false;
      if (minSqft > 0 && p.sqft && p.sqft < minSqft) return false;
      if (requireGarden && !p.has_garden) return false;
      if (requireLift && !p.has_lift && !p.reception_on_ground_floor) return false;
      if (requireAC && !p.has_ac) return false;
      if (excludeUnderfloorHeating && p.has_underfloor_heating) return false;
      if (disabledTypes.includes(p.property_type || 'Unknown')) return false;
      if (maxPricePerSqft > 0 && p.price_per_sqft && p.price_per_sqft > maxPricePerSqft) return false;
      if (maxCommuteMins > 0) {
        if (p.commute_mins !== undefined && p.commute_mins > maxCommuteMins) return false;
      }

      if (statusFilter.length > 0) {
        const propStatus = p.user_status || 'None';
        if (!statusFilter.includes(propStatus)) return false;
      }

      if (addedInLast > 0) {
        if (!p.listing_update) return false;
        const date = new Date(p.listing_update);
        if (isNaN(date.getTime())) return false;
        const now = new Date();
        const diffTime = Math.abs(now.getTime() - date.getTime());
        const diffDays = diffTime / (1000 * 60 * 60 * 24);
        if (diffDays > addedInLast) return false;
      }

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
  }, [scoredProperties, minScore, priceRange, minBeds, maxBeds, minSqft, requireGarden, requireLift, requireAC, excludeUnderfloorHeating, disabledTypes, keywordFilter, maxPricePerSqft, maxCommuteMins, addedInLast, showIgnored, hideViewed, viewedProperties, statusFilter]);

  const uniqueTypes = useMemo(() => {
    return Array.from(new Set(properties.map(p => p.property_type || 'Unknown'))).sort();
  }, [properties]);

  const sortedPinnedProperties = useMemo(() => {
    const pinned = properties.filter(p => p.pinned);
    const sorted = [...pinned];
    if (pinnedSortBy === 'score_desc') sorted.sort((a, b) => (b.score || 0) - (a.score || 0));
    else if (pinnedSortBy === 'price_asc') sorted.sort((a, b) => (a.price_pcm || 0) - (b.price_pcm || 0));
    else if (pinnedSortBy === 'price_desc') sorted.sort((a, b) => (b.price_pcm || 0) - (a.price_pcm || 0));
    else if (pinnedSortBy === 'date_desc') {
      sorted.sort((a, b) => {
        const da = a.listing_update ? new Date(a.listing_update).getTime() : 0;
        const db = b.listing_update ? new Date(b.listing_update).getTime() : 0;
        return db - da;
      });
    }
    return sorted;
  }, [properties, pinnedSortBy]);

  const resetFilters = () => {
    setMinScore(50);
    setPriceRange([0, 99999]);
    setMinBeds(1);
    setMaxBeds(5);
    setMinSqft(0);
    setRequireGarden(false);
    setRequireLift(false);
    setRequireAC(false);
    setExcludeUnderfloorHeating(false);
    setDisabledTypes([]);
    setKeywordFilter('');
    setMaxPricePerSqft(0);
    setMaxCommuteMins(0);
    setAddedInLast(0);
    setShowIgnored(false);
    setStatusFilter([]);
    setHideViewed(false);
  };

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

  const activeProp = useMemo(() => {
    return selectedProp ? scoredProperties.find(p => p.id === selectedProp.id) || selectedProp : null;
  }, [selectedProp, scoredProperties]);

  const images = getImages(activeProp);

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
          <button className="toggle-pinned-btn" onClick={() => { setShowPinnedPanel(!showPinnedPanel); setShowSettingsPanel(false); setShowItemPanel(false); }}>
            <List size={20} /> Pinned Properties
          </button>

          <button className="toggle-pinned-btn" onClick={() => { setShowItemPanel(!showItemPanel); setShowPinnedPanel(false); setShowSettingsPanel(false); }}>
            <MapIcon size={20} /> Large Item Constraints
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

          <button className="toggle-pinned-btn" onClick={() => { setShowSettingsPanel(!showSettingsPanel); setShowPinnedPanel(false); setShowItemPanel(false); }}>
            <Settings size={20} /> Scoring Configuration
          </button>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
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
              <label>Bedrooms</label>
              <div style={{ display: 'flex', gap: '10px' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>Min: {minBeds}</div>
                  <input 
                    type="range" min="1" max="5" value={minBeds} 
                    onChange={e => setMinBeds(Math.min(Number(e.target.value), maxBeds))} 
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>Max: {maxBeds === 5 ? '5+' : maxBeds}</div>
                  <input 
                    type="range" min="1" max="5" value={maxBeds} 
                    onChange={e => setMaxBeds(Math.max(Number(e.target.value), minBeds))} 
                  />
                </div>
              </div>
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
          </div>

          <div className="filter-group">
            <GoogleSelect 
              label="Added in the last"
              value={addedInLast}
              onChange={(val) => setAddedInLast(Number(val))}
              options={[
                { value: 0, label: 'Anytime' },
                { value: 1, label: '24 hours' },
                { value: 3, label: '3 days' },
                { value: 7, label: '7 days' },
                { value: 30, label: '1 month' }
              ]}
            />
          </div>

          <div className="toggle-group" onClick={() => setRequireGarden(!requireGarden)}>
            <div className={`toggle-switch ${requireGarden ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Require Garden</label>
          </div>

          <div className="toggle-group" onClick={() => setRequireLift(!requireLift)}>
            <div className={`toggle-switch ${requireLift ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Require Lift</label>
          </div>

          <div className="toggle-group" onClick={() => setRequireAC(!requireAC)}>
            <div className={`toggle-switch ${requireAC ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Require Air Conditioning</label>
          </div>

          <div className="toggle-group" onClick={() => setExcludeUnderfloorHeating(!excludeUnderfloorHeating)}>
            <div className={`toggle-switch ${excludeUnderfloorHeating ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Exclude Underfloor Heating</label>
          </div>

          <div className="toggle-group" onClick={() => setShowIgnored(!showIgnored)}>
            <div className={`toggle-switch ${showIgnored ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Show Ignored Properties</label>
          </div>

          <div className="toggle-group" onClick={() => setHideViewed(!hideViewed)}>
            <div className={`toggle-switch ${hideViewed ? 'active' : ''}`}></div>
            <label style={{ margin: 0, cursor: 'pointer' }}>Hide Viewed Properties</label>
          </div>

          <div className="filter-group">
            <label>User Status</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
              {['Interested', 'Contacted', 'Viewing', 'Offer', 'Rejected'].map(status => (
                <label key={status} className="checkbox-label" style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  <input 
                    type="checkbox" 
                    checked={statusFilter.includes(status)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setStatusFilter([...statusFilter, status]);
                      } else {
                        setStatusFilter(statusFilter.filter(s => s !== status));
                      }
                    }} 
                    style={{ accentColor: 'var(--accent)' }}
                  />
                  <span>{status}</span>
                </label>
              ))}
            </div>
          </div>


          <div className="filter-group">
            <label>Property Type</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
              {uniqueTypes.map(type => (
                <label key={type} className="checkbox-label" style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
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
          

        </div>
        
        <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <button 
            onClick={resetFilters}
            style={{ padding: '8px', background: 'transparent', border: '1px solid var(--glass-border)', borderRadius: '6px', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '0.85rem' }}
          >
            Reset Filters
          </button>
          <div style={{ textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
            Showing {filteredProperties.length} of {properties.length} properties
          </div>
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
        <div style={{ padding: '16px 24px 0 24px' }}>
          <GoogleSelect 
            label="Sort By"
            value={pinnedSortBy}
            onChange={(val) => setPinnedSortBy(val as string)}
            options={[
              { value: 'default', label: 'Default' },
              { value: 'score_desc', label: 'Score (Highest first)' },
              { value: 'price_asc', label: 'Price (Lowest first)' },
              { value: 'price_desc', label: 'Price (Highest first)' },
              { value: 'date_desc', label: 'Date Added (Newest first)' }
            ]}
          />
        </div>
        <div className="pinned-list">
          {sortedPinnedProperties.length === 0 ? (
            <div style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '40px 20px' }}>
              No pinned properties yet. Click a property and tap the star icon to pin it.
            </div>
          ) : (
            sortedPinnedProperties.map(p => (
              <div 
                key={p.id} 
                className="pinned-card" 
                onClick={() => { setSelectedProp(p); setCurrentImageIndex(0); }}
                onMouseEnter={() => setHoveredPinnedId(p.id)}
                onMouseLeave={() => setHoveredPinnedId(null)}
              >
                <div style={{ position: 'relative', width: '91.5px', height: '91.5px', flexShrink: 0 }}>
                  <img 
                    src={getImages(p)[0]} 
                    alt="Property" 
                    className="pinned-card-image"
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  />
                  {hoveredPinnedId === p.id && (
                    <button
                      className="pinned-card-close-btn"
                      onClick={(e) => { e.stopPropagation(); togglePin(p); }}
                    >
                      <X size={14} />
                    </button>
                  )}
                </div>
                <div className="pinned-card-content">
                  <div className="pinned-card-header">
                    <div className="pinned-card-address" title={p.raw_data?.display_address || p.raw_data?.address || p.description?.substring(0, 50) || 'Unknown Property'}>
                      {p.raw_data?.display_address || p.raw_data?.address || 'Unknown Property'}
                    </div>
                    <div className="pinned-card-score">
                      <span>{(Math.max(0, Math.min(100, p.score || 0)) / 20).toFixed(1)}</span>
                      <span style={{ color: '#fbbc04', fontSize: '14px' }}>★</span>
                      <span>({Math.round(p.score || 0)}/100)</span>
                    </div>
                    <div className="pinned-card-type">
                      <span>{p.property_type || 'Property'}</span>
                      <span style={{ fontWeight: 500, color: '#202124' }}>£{p.price_pcm} pcm</span>
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Settings Panel */}
      <div className={`pinned-panel ${showSettingsPanel ? 'open' : ''}`}>
        <div className="pinned-panel-header">
          <h2>Scoring Configuration</h2>
          <button className="close-btn" onClick={() => setShowSettingsPanel(false)}>
            <X size={24} />
          </button>
        </div>
        <div className="pinned-list" style={{ padding: '24px', gap: '24px', display: 'flex', flexDirection: 'column' }}>
          
          <div className="filter-group">
            <label style={{ fontSize: '1.1rem', color: '#202124', marginBottom: '8px' }}>Score Weighting</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {Object.entries(weights).filter(([key]) => key !== 'price' && key !== 'bedroom_size').map(([key, val]) => (
                <div key={key}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '4px' }}>
                    <span style={{ textTransform: 'capitalize', color: 'var(--text-secondary)' }}>{key.replace('_', ' ')}</span>
                    <span style={{ fontWeight: 500 }}>{val}</span>
                  </div>
                  <input 
                    type="range" min="0" max="40" step="1" value={val} 
                    onChange={e => setWeights({...weights, [key]: Number(e.target.value)})} 
                    style={{ width: '100%' }}
                  />
                </div>
              ))}
            </div>
          </div>

          <div style={{ height: '1px', background: 'var(--glass-border)', margin: '8px 0' }}></div>

          <div className="filter-group">
            <label style={{ fontSize: '1.1rem', color: '#202124', marginBottom: '8px' }}>Penalties</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {Object.entries(penalties).filter(([key]) => !['ugly_exterior', 'small_reception', 'virtual_staging'].includes(key)).map(([key, val]) => (
                <div key={key}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '4px' }}>
                    <span style={{ textTransform: 'capitalize', color: 'var(--text-secondary)' }}>{key.replace(/_/g, ' ')}</span>
                    <span style={{ fontWeight: 500 }}>{val}</span>
                  </div>
                  <input 
                    type="range" min="-50" max="0" step="1" value={val as number} 
                    onChange={e => setPenalties({...penalties, [key]: Number(e.target.value)})} 
                    style={{ width: '100%' }}
                  />
                </div>
              ))}
            </div>
          </div>

          <div style={{ height: '1px', background: 'var(--glass-border)', margin: '8px 0' }}></div>

          <div className="filter-group">
            <label style={{ fontSize: '1.1rem', color: '#202124', marginBottom: '8px' }}>Size Preferences</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '4px' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Optimal Total Sqft</span>
                  <span style={{ fontWeight: 500 }}>{scoringParams.optimal_total_sqft} sqft</span>
                </div>
                <input type="range" min="500" max="3000" step="50" value={scoringParams.optimal_total_sqft} onChange={e => setScoringParams({...scoringParams, optimal_total_sqft: Number(e.target.value)})} style={{ width: '100%' }} />
              </div>

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '4px' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>High Ceiling Threshold</span>
                  <span style={{ fontWeight: 500 }}>{scoringParams.high_ceiling_threshold_m} m</span>
                </div>
                <input type="range" min="2.2" max="3.5" step="0.1" value={scoringParams.high_ceiling_threshold_m} onChange={e => setScoringParams({...scoringParams, high_ceiling_threshold_m: Number(e.target.value)})} style={{ width: '100%' }} />
              </div>

            </div>
          </div>
        </div>
      </div>

      {/* Item Constraints Panel */}
      <div className={`pinned-panel ${showItemPanel ? 'open' : ''}`}>
        <div className="pinned-panel-header">
          <h2>Large Item Constraints</h2>
          <button className="close-btn" onClick={() => setShowItemPanel(false)}>
            <X size={24} />
          </button>
        </div>
        <div className="pinned-list" style={{ padding: '24px', gap: '24px', display: 'flex', flexDirection: 'column' }}>
          <div className="filter-group">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <label style={{ fontSize: '1.1rem', color: '#202124', margin: 0 }}>Custom Items</label>
              <button 
                onClick={() => setCustomItems([...customItems, { id: Date.now().toString(), name: 'New Item', length: 1.0, width: 1.0 }])}
                style={{ padding: '4px 8px', fontSize: '0.8rem', background: 'var(--accent)', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
              >
                + Add Item
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {customItems.map((item: any, idx: number) => (
                <div key={item.id} style={{ border: '1px solid var(--glass-border)', padding: '12px', borderRadius: '8px', background: 'rgba(255,255,255,0.5)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>Item {idx + 1}</span>
                    <button 
                      onClick={() => setCustomItems(customItems.filter((i: any) => i.id !== item.id))}
                      style={{ background: 'none', border: 'none', color: '#d93025', cursor: 'pointer', padding: '4px' }}
                    >
                      <X size={16} />
                    </button>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <div>
                      <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Name</span>
                      <input 
                        type="text" 
                        className="search-input" 
                        value={item.name}
                        onChange={e => setCustomItems(customItems.map((i: any) => i.id === item.id ? { ...i, name: e.target.value } : i))}
                        style={{ padding: '6px', width: '100%', boxSizing: 'border-box', marginTop: '2px' }}
                      />
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <div style={{ flex: 1 }}>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Length (m)</span>
                        <input 
                          type="number" step="0.1" className="search-input" 
                          value={item.length}
                          onChange={e => setCustomItems(customItems.map((i: any) => i.id === item.id ? { ...i, length: Number(e.target.value) } : i))}
                          style={{ padding: '6px', width: '100%', boxSizing: 'border-box', marginTop: '2px' }}
                        />
                      </div>
                      <div style={{ flex: 1 }}>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Width (m)</span>
                        <input 
                          type="number" step="0.1" className="search-input" 
                          value={item.width}
                          onChange={e => setCustomItems(customItems.map((i: any) => i.id === item.id ? { ...i, width: Number(e.target.value) } : i))}
                          style={{ padding: '6px', width: '100%', boxSizing: 'border-box', marginTop: '2px' }}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              ))}
              {customItems.length === 0 && (
                <div style={{ textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.9rem', padding: '20px 0' }}>
                  No items configured.
                </div>
              )}
            </div>
          </div>
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
            <MapEvents onClick={() => {
              setSelectedProp(null);
              setShowPinnedPanel(false);
              setShowSettingsPanel(false);
              setShowItemPanel(false);
            }} />
            {filteredProperties.filter(p => showPinnedPanel ? p.pinned : true).map(p => (
              <Marker 
                key={p.id} 
                position={[p.latitude!, p.longitude!]}
                icon={createCustomIcon(p.score || 0, p.ignored, p.pinned || false, isFresh(p.listing_update), viewedProperties.includes(p.id), hoveredPinnedId === p.id)}
                zIndexOffset={Math.round((p.score || 0) * 1000) + (p.pinned ? 100000 : 0) - (viewedProperties.includes(p.id) ? 500 : 0)}
                riseOnHover={true}
                eventHandlers={{
                  click: (e: any) => {
                    if (e.originalEvent) {
                      e.originalEvent.stopPropagation();
                    }
                    setSelectedProp(p); 
                    setCurrentImageIndex(0); 
                    setShowPinnedPanel(false);
                    setShowSettingsPanel(false);
                    setShowItemPanel(false);
                    if (!viewedProperties.includes(p.id)) {
                      setViewedProperties([...viewedProperties, p.id]);
                    }
                  },
                }}
              />
            ))}
            {pois.map((poi: any, index: number) => (
              <Marker
                key={`poi-${index}`}
                position={[poi.lat, poi.lng]}
                icon={createPoiIcon(poi.type)}
                eventHandlers={{
                  click: (e: any) => {
                    if (e.originalEvent) {
                      e.originalEvent.stopPropagation();
                    }
                    setShowPinnedPanel(false);
                    setShowSettingsPanel(false);
                    setShowItemPanel(false);
                  }
                }}
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
        <div className={`property-drawer ${activeProp ? 'open' : ''}`}>
          {activeProp && (
            <>
              <div className="drawer-hero-container">
                {isFresh(activeProp.listing_update) && <div className="fresh-badge drawer-fresh-badge">NEW</div>}
                <img 
                  src={images[currentImageIndex]} 
                  alt={activeProp.raw_data?.display_address || 'Property'} 
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
                <button 
                  className="drawer-close-btn-image" 
                  onClick={() => setSelectedProp(null)}
                >
                  <X size={20} />
                </button>
              </div>

              <div className="drawer-content-scrollable">
                <div className="drawer-title-section">
                  <h1 className="drawer-title">
                    {activeProp.raw_data?.display_address || activeProp.raw_data?.address || 'Property Details'}
                  </h1>
                  <div className="drawer-subtitle">
                    <span className="drawer-score">{(Math.max(0, Math.min(100, activeProp.score || 0)) / 20).toFixed(1)}</span>
                    <span className="drawer-stars"><StarRating score100={activeProp.score || 0} /></span>
                    <span className="drawer-score" style={{ marginLeft: 4 }}>({Math.round(activeProp.score || 0)} / 100)</span>
                    <span> · £{activeProp.price_pcm} pcm</span>
                  </div>
                  <div className="drawer-type">
                    {activeProp.property_type || 'Property'} · {activeProp.bedrooms} Bedrooms {activeProp.sqft ? `· ${activeProp.sqft} sqft` : ''}
                  </div>
                </div>

                <div className="drawer-tabs">
                  <div className={`drawer-tab ${activeTab === 'Overview' ? 'active' : ''}`} onClick={() => setActiveTab('Overview')}>Overview</div>
                  <div className={`drawer-tab ${activeTab === 'Scorecard' ? 'active' : ''}`} onClick={() => setActiveTab('Scorecard')}>Scorecard</div>
                </div>

                {activeTab === 'Overview' && (
                  <>
                    <div className="drawer-actions">
                      <button className="drawer-action-item" onClick={() => togglePin(activeProp)}>
                        <div className="drawer-action-icon-wrapper" style={{ background: activeProp.pinned ? '#e6f4ea' : '#e8f0fe', color: activeProp.pinned ? '#137333' : '#1a73e8' }}>
                          <Pin size={20} fill={activeProp.pinned ? 'currentColor' : 'none'} />
                        </div>
                        <span>{activeProp.pinned ? 'Saved' : 'Save'}</span>
                      </button>
                      <button className="drawer-action-item" onClick={() => window.open(activeProp.raw_data?.url, '_blank')}>
                        <div className="drawer-action-icon-wrapper">
                          <ExternalLink size={20} />
                        </div>
                        <span>Rightmove</span>
                      </button>
                      <button className="drawer-action-item" onClick={() => updatePropertyDetails(activeProp.id, { ignored: !activeProp.ignored })}>
                        <div className="drawer-action-icon-wrapper" style={{ background: activeProp.ignored ? '#fce8e6' : '#f1f3f4', color: activeProp.ignored ? '#d93025' : '#5f6368' }}>
                          <Trash2 size={20} fill={activeProp.ignored ? 'currentColor' : 'none'} />
                        </div>
                        <span>{activeProp.ignored ? 'Restore' : 'Ignore'}</span>
                      </button>
                    </div>

                    <div className="drawer-order-btn-wrapper">
                      <button className="drawer-order-btn" onClick={() => updatePropertyDetails(activeProp.id, { user_status: 'Contacted' })}>
                        <MessageSquare size={18} /> Contact Agent
                      </button>
                    </div>

                    <div className="drawer-list-details">
                      <div className="drawer-list-item">
                        <div className="drawer-list-icon"><List size={20} color="#70757a" /></div>
                        <div className="drawer-list-text" style={{ flex: 1 }}>
                          <GoogleSelect
                            label="Status"
                            value={activeProp.user_status || 'None'}
                            onChange={(val) => updatePropertyDetails(activeProp.id, { user_status: val as string })}
                            options={[
                              { value: "None", label: "None" },
                              { value: "Interested", label: "Interested" },
                              { value: "Contacted", label: "Contacted Agent" },
                              { value: "Viewing", label: "Viewing Booked" },
                              { value: "Offer", label: "Made Offer" },
                              { value: "Rejected", label: "Rejected" }
                            ]}
                          />
                        </div>
                      </div>
                    </div>

                    <div className="drawer-list-details">
                      <div className="drawer-list-item" style={{ alignItems: 'flex-start' }}>
                        <div className="drawer-list-icon"><CheckCircle2 size={20} color="#188038" /></div>
                        <div className="drawer-list-text" style={{ lineHeight: 1.5 }}>
                          {activeProp.breakdown?.pros?.length ? (
                            activeProp.breakdown.pros.map((p, i) => (
                              <div key={i}>✓ {p}</div>
                            ))
                          ) : (
                            <div>No pros listed</div>
                          )}
                        </div>
                      </div>
                      
                      {(activeProp.breakdown?.cons?.length || 0) > 0 && (
                        <div className="drawer-list-item" style={{ alignItems: 'flex-start' }}>
                          <div className="drawer-list-icon"><XCircle size={20} color="#d93025" /></div>
                          <div className="drawer-list-text" style={{ lineHeight: 1.5 }}>
                            {activeProp.breakdown!.cons!.map((c, i) => (
                              <div key={i}>✗ {c}</div>
                            ))}
                          </div>
                        </div>
                      )}

                      <div className="drawer-list-item">
                        <div className="drawer-list-icon"><MapPin size={20} /></div>
                        <div className="drawer-list-text">
                          <a 
                            href={activeProp.latitude && activeProp.longitude 
                              ? `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${activeProp.latitude},${activeProp.longitude}`
                              : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(activeProp.raw_data?.display_address || activeProp.raw_data?.address || '')}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ color: 'inherit', textDecoration: 'underline' }}
                          >
                            {activeProp.raw_data?.display_address || 'Address hidden'}
                          </a>
                        </div>
                      </div>
                      
                      <div className="drawer-list-item">
                        <div className="drawer-list-icon"><Clock size={20} /></div>
                        <div className="drawer-list-text">
                          {activeProp.commute_mins ? `${Math.ceil(activeProp.commute_mins)} mins commute to office` : 'Commute unknown'}
                        </div>
                      </div>

                      <div className="drawer-list-item">
                        <div className="drawer-list-icon" style={{ fontSize: '18px', display: 'flex', alignItems: 'center' }}>£</div>
                        <div className="drawer-list-text">
                          {activeProp.price_per_sqft ? `£${activeProp.price_per_sqft} per sqft` : 'Price per sqft unknown'}
                        </div>
                      </div>

                      {activeProp.raw_data?.url && (
                        <div className="drawer-list-item">
                          <div className="drawer-list-icon"><Globe size={20} /></div>
                          <div className="drawer-list-text">
                            <a href={activeProp.raw_data.url} target="_blank" rel="noopener noreferrer">rightmove.co.uk</a>
                          </div>
                        </div>
                      )}

                      <div className="drawer-list-item">
                        <div className="drawer-list-icon"><Clock size={20} /></div>
                        <div className="drawer-list-text">
                          Listed {formatListedDate(activeProp.listing_update)}
                        </div>
                      </div>
                    </div>

                    <div className="drawer-updates-section">
                      <h3>Private Notes</h3>
                      <NoteEditor 
                        propId={activeProp.id} 
                        initialNote={activeProp.user_note || ''} 
                        onSave={(id, note) => updatePropertyDetails(id, { user_note: note })} 
                      />
                    </div>

                    {activeProp.raw_data?.floorplans && activeProp.raw_data.floorplans.length > 0 && (
                      <div className="drawer-updates-section">
                        <h3>Floorplan</h3>
                        <div 
                          onClick={() => setIsFloorplanExpanded(true)}
                          style={{ cursor: 'pointer', borderRadius: '8px', overflow: 'hidden', border: '1px solid var(--glass-border)', position: 'relative' }}
                        >
                          <img 
                            src={typeof activeProp.raw_data.floorplans[0] === 'string' ? activeProp.raw_data.floorplans[0] : activeProp.raw_data.floorplans[0].url} 
                            alt="Floorplan" 
                            style={{ width: '100%', display: 'block', maxHeight: '200px', objectFit: 'contain', backgroundColor: '#fff' }}
                          />
                          <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: 0, transition: 'opacity 0.2s' }} onMouseEnter={(e) => e.currentTarget.style.opacity = '1'} onMouseLeave={(e) => e.currentTarget.style.opacity = '0'}>
                            <span style={{ backgroundColor: 'rgba(0,0,0,0.7)', color: 'white', padding: '8px 16px', borderRadius: '20px', fontSize: '14px' }}>Click to expand</span>
                          </div>
                        </div>
                      </div>
                    )}

                    {activeProp.floorplan_graph && customItems.length > 0 && (
                      <div className="drawer-updates-section" style={{ marginTop: '16px' }}>
                        <h3>Fit Verification</h3>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                          {customItems.map((item: any, idx: number) => {
                            const result = validateFit(activeProp.floorplan_graph, item.length, item.width);
                            return (
                              <div key={item.id || idx} style={{
                                padding: '12px',
                                borderRadius: '8px',
                                backgroundColor: result.fits ? '#e6f4ea' : '#fce8e6',
                                border: `1px solid ${result.fits ? '#ceead6' : '#fad2cf'}`,
                                color: result.fits ? '#137333' : '#c5221f',
                                display: 'flex',
                                alignItems: 'flex-start',
                                gap: '12px'
                              }}>
                                {result.fits ? <CheckCircle2 size={24} style={{ flexShrink: 0 }} /> : <XCircle size={24} style={{ flexShrink: 0 }} />}
                                <div>
                                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>
                                    {item.name}: {result.fits ? 'Item Fits!' : 'Fit Issue Detected'}
                                  </div>
                                  <div style={{ fontSize: '0.9rem', lineHeight: 1.4 }}>
                                    {result.reason}
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {activeProp.floorplan_graph?.rooms && (
                      <div className="drawer-updates-section" style={{ marginTop: '16px' }}>
                        <h3>Natural Light Profile</h3>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                          {activeProp.floorplan_graph.rooms
                            .filter(r => ['reception', 'kitchen', 'bedroom'].includes(r.room_type))
                            .sort((a, b) => (b.sunlight_hours || 0) - (a.sunlight_hours || 0))
                            .map((room, idx) => (
                              <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px', backgroundColor: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                <span style={{ fontWeight: 500 }}>{room.name}</span>
                                <span style={{ color: 'var(--text-secondary)' }}>
                                  {room.sunlight_hours && room.sunlight_hours >= 4 ? '☀️ ' : (room.sunlight_hours && room.sunlight_hours > 0 ? '⛅ ' : '')}
                                  {room.sunlight_hours ? `${room.sunlight_hours} hrs/day` : 'Unknown'}
                                </span>
                              </div>
                            ))}
                        </div>
                      </div>
                    )}
                  </>
                )}

                {activeTab === 'Scorecard' && activeProp.breakdown?.scorecard && (
                  <div className="drawer-updates-section">
                    <h3>Score Breakdown</h3>
                    <div className="drawer-score-card">
                      {Object.entries(activeProp.breakdown.scorecard).map(([key, value]) => {
                        const context = getScoreContext(key, activeProp, weights, scoringParams, priceRange);
                        return (
                          <div key={key} style={{ marginBottom: '16px' }}>
                            <div className="drawer-score-item" style={{ marginBottom: '4px' }}>
                              <span style={{ textTransform: 'capitalize', fontWeight: 600 }}>{key.replace(/_/g, ' ')}</span>
                              <span className={`drawer-score-val ${value > 0 ? 'pos' : (value < 0 ? 'neg' : '')}`}>
                                {value > 0 ? '+' : ''}{value} pts
                              </span>
                            </div>
                            {context && (
                              <div style={{ fontSize: '13px', color: '#70757a', lineHeight: 1.4 }}>
                                {context}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Floorplan Expanded View */}
      {isFloorplanExpanded && activeProp?.raw_data?.floorplans && activeProp.raw_data.floorplans.length > 0 && (
        <div 
          style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, zIndex: 99999, backgroundColor: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }}
          onClick={() => setIsFloorplanExpanded(false)}
        >
          <button 
            style={{ position: 'absolute', top: '20px', right: '20px', background: 'white', border: 'none', borderRadius: '50%', width: '40px', height: '40px', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', boxShadow: '0 2px 10px rgba(0,0,0,0.2)' }}
            onClick={() => setIsFloorplanExpanded(false)}
          >
            <X size={24} color="#000" />
          </button>
          <img 
            src={typeof activeProp.raw_data.floorplans[0] === 'string' ? activeProp.raw_data.floorplans[0] : activeProp.raw_data.floorplans[0].url} 
            alt="Floorplan Expanded" 
            style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', backgroundColor: '#fff', borderRadius: '4px' }}
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}

export default App;
