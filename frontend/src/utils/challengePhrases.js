export const challengePhrases = [
  "Verify my identity",
  "Open the secure door",
  "Please unlock my account",
  "Today is a beautiful day",
  "Artificial intelligence is amazing",
  "Blue sky above",
  "Voice and lips match",
  "Access granted successfully",
  "Please confirm my voice print",
  "Welcome to the primary server",
  "Liveness detection is running",
  "The camera is active now",
  "Keep your eyes on me",
  "A warm summer breeze",
  "Fresh coffee in the morning",
  "Look straight at the screen",
  "Speak clearly into the microphone",
  "We appreciate your cooperation",
  "The sunset was beautiful tonight",
  "Have a great weekend ahead",
  "Practice makes perfect every time",
  "Bright stars shine in the night",
  "Unlock the secondary dashboard",
  "Security is our highest priority",
  "A smooth sea never made a skilled sailor",
  "Believe you can and you will",
  "Every moment is a fresh beginning",
  "Kindness is a language everyone understands",
  "Let your light shine bright",
  "Simple is better than complex",
  "Create a secure digital environment",
  "Database connection is fully verified",
  "Upload the encrypted backup file",
  "Check the network signal strength",
  "Initialize the system handshake",
  "Grant full administrator access",
  "Restore the original configuration",
  "Synchronize all user settings",
  "Update the local security certificate",
  "Input your safe code here",
  "A journey of a thousand miles starts",
  "Actions speak louder than words",
  "An apple a day keeps the doctor",
  "Better late than never",
  "Cleanliness is next to godliness",
  "Don't count your chickens before they hatch",
  "Easy come easy go",
  "Fortune favors the bold",
  "Honesty is the best policy",
  "Knowledge is power and strength",
  "No pain no gain",
  "Out of sight out of mind",
  "Two heads are better than one",
  "When in Rome do as Romans do",
  "You can't judge a book by cover",
  "Always look on the bright side",
  "Be the change you wish to see",
  "Do what you love every day",
  "Follow your heart and mind",
  "Happiness is a warm cup",
  "Live life to the fullest",
  "Make every single day count",
  "Never give up on your dreams",
  "Stay positive and work hard",
  "Think outside the box",
  "Today is a brand new day",
  "Write down your ultimate goals",
  "You are capable of amazing things",
  "Breathe in clean air slowly",
  "Walk along the quiet shore",
  "Enjoy the little things in life",
  "Find peace in the quiet moments",
  "Listen to the birds singing sweetly",
  "Nature is the best medicine",
  "Smell the fresh red roses",
  "Take a walk in the forest",
  "Watch the colorful autumn leaves",
  "A cup of hot green tea",
  "A slice of fresh lemon pie",
  "Bake some delicious chocolate cookies",
  "Cooking dinner for my family",
  "Fresh fruits are very healthy",
  "Home is where the heart is",
  "I love the smell of rain",
  "Learning to play the acoustic guitar",
  "Reading a fascinating adventure book",
  "Travelling to new beautiful places",
  "Warm soup on a cold winter",
  "A shiny silver pocket watch",
  "Gold coins in the wooden chest",
  "Lost in the deep blue sea",
  "Mapping the northern stars tonight",
  "The ancient castle on the hill",
  "The lighthouse guides the ships",
  "The treasure map is hidden well",
  "Whispers in the quiet wind",
  "A fast computer processor chip",
  "Connecting to the cloud servers",
  "Designing a modern user interface",
  "Programming is a creative process",
  "Software development requires deep concentration",
  "Technology connects people around the world"
];

/**
 * Generates a unique authentication session ID.
 * @returns {string} Unique session ID
 */
export const generateSessionId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `session-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
};

/**
 * Selects a random challenge phrase that is not in the list of recent IDs.
 * @param {Array<number>} recentIds List of recent phrase indices to exclude
 * Each challenge is deliberately short enough to read naturally within the
 * five-second recording window, while a fresh four-digit code prevents reuse.
 *
 * @returns {Object} { id: number, phrase: string, number: string }
 */
export const getRandomChallengePhrase = (recentIds = []) => {
  const total = challengePhrases.length;
  if (total === 0) return { id: -1, phrase: "" };
  
  // Safeguard: if recentIds has excluded all available phrases, clear it
  const effectiveRecent = recentIds.length >= total ? [] : recentIds;
  
  let selectedId = null;
  let attempts = 0;
  const maxAttempts = 200;
  
  do {
    selectedId = Math.floor(Math.random() * total);
    attempts++;
    if (attempts > maxAttempts) {
      break;
    }
  } while (effectiveRecent.includes(selectedId));
  
  const challengeNumber = String(Math.floor(1000 + Math.random() * 9000));

  return {
    id: selectedId,
    phrase: `Please read the number ${challengeNumber}.`,
    number: challengeNumber
  };
};
