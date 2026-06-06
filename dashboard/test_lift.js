import { initializeApp } from 'firebase/app';
import { getFirestore, collection, getDocs } from 'firebase/firestore';

const firebaseConfig = {
  apiKey: "AIzaSyAb5pGahalTWkvvHkFuaX-DHpFPLBouhR8",
  authDomain: "property-pinger.firebaseapp.com",
  projectId: "property-pinger",
  storageBucket: "property-pinger.firebasestorage.app",
  messagingSenderId: "860669954277",
  appId: "1:860669954277:web:56177798dac9fce5036c0c"
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

async function checkLifts() {
  const snapshot = await getDocs(collection(db, 'properties'));
  let total = 0;
  let withLift = 0;
  let liftTrueVal = 0;
  let descriptionMentionsLift = 0;

  snapshot.forEach(doc => {
    const data = doc.data();
    total++;
    const lift = data.has_lift;
    
    if (lift === true) liftTrueVal++;
    
    const desc = data.description || data.raw_data?.description || "";
    if (desc.toLowerCase().includes('lift') || desc.toLowerCase().includes('elevator')) {
      descriptionMentionsLift++;
    }
  });

  console.log(`Total properties: ${total}`);
  console.log(`Has lift (top level): ${liftTrueVal}`);
  console.log(`Description mentions lift/elevator: ${descriptionMentionsLift}`);
}

checkLifts().then(() => process.exit(0)).catch(e => { console.error(e); process.exit(1); });
