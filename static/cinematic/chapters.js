export const chapters = [
  {
    "id": "emancipator",
    "eyebrow": "PROJECT / 001",
    "title": [
      "Nanoplasmatic",
      "Electroparticle",
      "Black Hole Emancipator"
    ],
    "description": "",
    "badge": null,
    "notes": [
      "[WAIT 2 beats. Deliver the title completely seriously. SPACE only after the full name.]",
      "SAY: Today, I’m introducing the nanoplasmatic electroparticle black hole emancipator.",
      "Keep the opening serious. No visible Threshold brand, wink, prototype disclaimer, or joke caption before the name reveal."
    ],
    "time": "0:00–0:09",
    "caption": "",
    "kind": "intro"
  },
  {
    "id": "reveal",
    "eyebrow": "ETHAN / FRONTIER CASCADIA",
    "title": [
      "THRESHOLD"
    ],
    "description": "",
    "badge": null,
    "notes": [
      "[WAIT for the letters to unscramble into THRESHOLD. Say the name after it settles. Hold 1 beat. SPACE.]",
      "SAY: Or: Threshold.",
      "The letter scramble is a title effect, not a literal anagram. This is the first visible project-name reveal."
    ],
    "time": "0:09–0:15",
    "caption": "",
    "kind": "reveal"
  },
  {
    "id": "a-door",
    "eyebrow": "A SMALL REVISION",
    "title": [
      "I cannot",
      "build that."
    ],
    "description": "So I started with a door.",
    "badge": null,
    "notes": [
      "[Pause after ‘I cannot build that.’ Let the joke land; do not explain it. SPACE.]",
      "SAY: I cannot build that. The black hole part was a problem. So I started with a door."
    ],
    "time": "0:15–0:25",
    "caption": "",
    "kind": "punchline"
  },
  {
    "id": "door-state",
    "eyebrow": "THE CONTACT SENSOR",
    "title": [
      "Open.",
      "Closed."
    ],
    "description": "Door sensors do one job well: report open or closed. But an open doorway can stay busy without changing state.",
    "badge": null,
    "notes": [
      "[Let the open/closed contrast sit. SPACE after ‘went through.’]",
      "SAY: A magnetic contact sensor tells you whether a door is open or closed. That’s useful. But if the door stays open, that state alone doesn’t tell you whether someone just went through.",
      "This is a fair comparison with a contact sensor’s door-state signal, not a claim that all existing security systems lack motion sensing."
    ],
    "time": "0:25–0:43",
    "caption": "",
    "kind": "standard"
  },
  {
    "id": "sensor-comparison",
    "eyebrow": "THE COMPARISON",
    "title": [
      "Beyond open",
      "and closed."
    ],
    "description": "One open door. Three people enter. The contact sensor still reports “open.”",
    "badge": {
      "text": "PRODUCT VISION",
      "tone": "future"
    },
    "notes": [
      "[Allow about 15 seconds. Lead with the three-person example. The column heading frames The Threshold vision once. SPACE.]",
      "SAY: One open door. Three people walk in. A magnetic contact sensor still reports open. That gap is why I’m building Threshold: not just whether the door moved, but what happened at the entrance.",
      "Compare only a magnetic contact sensor’s door-state signal, not all security systems. Wi-Fi physical sensing remains unverified. Entry counting and fall sensing are not implemented; an entry count would not by itself establish occupancy."
    ],
    "time": "0:43–0:58",
    "caption": "The missing information is activity.",
    "kind": "comparison",
    "comparison": [
      {
        "label": "Signal",
        "contact": "Open / closed",
        "threshold": "Read changes in Wi-Fi"
      },
      {
        "label": "Door left open",
        "contact": "No new door-state event",
        "threshold": "Sense activity while open"
      },
      {
        "label": "Count room entries",
        "contact": "Not from door state alone",
        "threshold": "Count entries"
      },
      {
        "label": "Fall-related motion",
        "contact": "Not from door state alone",
        "threshold": "Recognize fall-like patterns"
      }
    ]
  },
  {
    "id": "the-question",
    "eyebrow": "A DIFFERENT QUESTION",
    "title": [
      "The door is open.",
      "Did anyone pass?"
    ],
    "description": "A doorway can stay open while people come and go. The idea: turn that missing activity into a clear event you can act on.",
    "badge": {
      "text": "ILLUSTRATED DEMONSTRATION",
      "tone": "neutral"
    },
    "notes": [
      "[Let the illustrated crossing and red cue land. SPACE after ‘moves.’]",
      "SAY: An entrance alert should tell you something happened at the entrance. Someone passes through. One clear event to act on—even if the door never moves.",
      "The red alert is illustrative. Keep ILLUSTRATED DEMONSTRATION visible throughout it. This animation is not the real app, hardware sensing, or a detector result.",
      "Physical crossing discrimination, direction, occupancy, and exact-zone localization have not been demonstrated. No superiority claim is made."
    ],
    "time": "0:58–1:15",
    "caption": "",
    "kind": "standard"
  },
  {
    "id": "the-signal",
    "eyebrow": "THE CONCEPT",
    "title": [
      "Movement changes",
      "the signal."
    ],
    "description": "Wi-Fi signals change as people move. The idea is to read those changes, without recording a video. Start at the doorway; explore the room beyond it.",
    "badge": {
      "text": "ILLUSTRATION",
      "tone": "future"
    },
    "notes": [
      "[Let the signal field support the idea. Keep ILLUSTRATION visible. SPACE.]",
      "SAY: The idea is Wi-Fi sensing: movement changes how radio signals travel. Read those changes as clues, rather than record a video. Start near an entrance, then explore the room beyond it. Doorways are the starting point, not the limit of the vision.",
      "CSI means channel state information. The README records real TX sends and successful MAC callbacks, but zero exported received CSI rows. TX success is not sensing. Rendered waves and geometry are not measured coverage or a tracked person.",
      "Beyond-doorway extent is part of the vision, not a measured radius. Do not infer guaranteed coverage through walls, exact localization, or compatibility with every router from this illustration."
    ],
    "time": "1:15–1:35",
    "caption": "From the doorway into the room: the sensing concept.",
    "kind": "standard"
  },
  {
    "id": "built-software",
    "eyebrow": "BUILT SOFTWARE",
    "title": [
      "One shared alarm."
    ],
    "description": "Native iPhone controls and a local console, connected to one shared alarm. Calibrate, arm, acknowledge, and review the event journal.",
    "badge": {
      "text": "ARCHIVED TEST · SYNTHETIC",
      "tone": "test"
    },
    "notes": [
      "[Give the actual archived interface capture room to be seen. Point out TEST. SPACE.]",
      "SAY: Here’s what I’ve built: native iPhone controls, a local console, and one shared alarm. This archived Simulator view shows the software workflow using synthetic TEST input. The transmitter has made real Wi-Fi sends; physical sensing still needs validation.",
      "Use the selected assets/native-test.png: an archived iPhone Simulator Gate-4 screenshot; see assets/PROVENANCE.json. It is not a physical iPhone capture or current alarm state. Do not invent, repaint, or animate detector state inside the image. If the capture is unavailable, say so instead of claiming to show it.",
      "The README’s prior software verification supports the claim. This deck build is not a new software, physical-device, radio, or presentation verification run.",
      "This is the single spoken proof boundary. State it plainly once, then return to the product story. The archive is synthetic TEST; the physical sensing chain still needs validation."
    ],
    "time": "1:35–1:56",
    "caption": "Recorded Simulator interface · synthetic TEST",
    "kind": "product"
  },
  {
    "id": "console-handoff",
    "eyebrow": "THE HANDOFF",
    "title": [
      "To the console."
    ],
    "description": "Move from the presentation to the selected local TEST console. Calibrate, arm, trigger a synthetic event, then acknowledge and disarm.",
    "badge": {
      "text": "LOCAL HANDOFF",
      "tone": "neutral"
    },
    "notes": [
      "[Choose ONE script: unavailable fallback below, or the configured-console alternative. The deck waits for you.]",
      "IF UNAVAILABLE — SAY: The console is separate from the deck. It isn’t connected for this run, so the archived TEST view is our demo. Now, here’s the bigger idea.",
      "IF CONFIGURED AND CONFIRMED TEST — SAY: I’ll use the selected TEST console: calibrate, arm, trigger a synthetic event, acknowledge, then disarm. One shared alarm, and a record of what happened.",
      "Open only the local console URL explicitly selected in setup. A configured link is not proof of a running or healthy backend. If it cannot be used, say the fallback. Never guess a port, embed a token, switch source mode, or start another server.",
      "Only the lead operates an approved TEST session. Check its actual TEST label and control lease. Use the real console’s labelled synthetic action; deck animation buttons do not trigger it. Finish independently confirmed DISARMED, actor stopped, calls disabled. Return to this deck and press SPACE. If state is unknown, stop and let the lead resolve it."
    ],
    "time": "1:56–2:20",
    "caption": "Presentation and console are separate.",
    "kind": "demo"
  },
  {
    "id": "fall-research",
    "eyebrow": "BEYOND THE ENTRANCE",
    "title": [
      "A fall shouldn’t",
      "go unnoticed."
    ],
    "description": "For someone who falls alone, being noticed sooner matters. Imagine a possible fall prompting a check-in, so help can reach them faster.",
    "badge": {
      "text": "PRODUCT VISION",
      "tone": "future"
    },
    "notes": [
      "[Give the human stakes a beat. Speak as an opportunity, not a working medical claim. Keep PRODUCT VISION visible. SPACE.]",
      "SAY: When someone falls alone, getting help sooner can save a life. Imagine movement sensing that flags a possible fall and prompts a check-in—without turning the room into a camera feed. That’s where I want to take Threshold: awareness that helps people, not just alerts about doors.",
      "This is a research direction, not a fall detector, medical claim, emergency service, or safety guarantee. No detected motion does not mean unoccupied or safe. Do not imply falls, occupancy, exact tracking, or vital signs are built."
    ],
    "time": "2:20–2:40",
    "caption": "Faster help can save a life.",
    "kind": "future"
  },
  {
    "id": "start-here",
    "eyebrow": "THRESHOLD",
    "title": [
      "Every entrance.",
      "One shared view."
    ],
    "description": "ESP32 transmitter/receiver pairs at every doorway, feeding one shared local alarm. From isolated door states to a building-wide view of activity.",
    "badge": {
      "text": "PRODUCT VISION",
      "tone": "future"
    },
    "notes": [
      "[Hold the building-wide vision on the final frame. Stop. No automatic restart.]",
      "SAY: The full vision: ESP32 transmitter and receiver pairs at every doorway, connected to one shared local alarm. Start at the entrance, extend into the spaces beyond, and build toward protection across the whole building. That’s Threshold.",
      "The full ESP32 network is a product vision, not a deployed or verified whole-building safety system. Nodes at doors do not by themselves guarantee coverage in every room. Placement, received CSI, physical trials, false alerts, and reliability need evaluation. Do not promise fall detection, emergency delivery, or complete protection."
    ],
    "time": "2:40–3:00",
    "caption": "Start at the entrance. Think building-wide.",
    "kind": "outro"
  }
];
