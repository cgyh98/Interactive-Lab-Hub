# Observant Systems: Overnight Sensation

**Team:** 

**Members:** Jacey Hu (ch2296, kakiko1111), Gabriela Yaulli (cgy4, cgyh98) , Edmond Kong (eck67, edmkong)

**Project name:** Door Watch

**Acknowledgements:** I used Claude (Anthropic) to help me set up VNC, fix the locale error, troubleshoot the git merge, write and debug the Door Watch scripts (`door_watch.py`, `door_morning.py`, `pitft.py`), and draft and edit the English in this write-up.

---

# Part 1

## Part A. Set up your Pi for on-device AI

I created a virtual environment in the Lab 5 folder and installed the requirements with `uv`. Then I exported the YOLO26 nano model to NCNN.

The first export printed a `UnicodeEncodeError` after almost every message, because my shell couldn't print emoji. The export itself still worked. I fixed the locale in `~/.bashrc` as the lab suggests, and after that the output was clean.

**Test on the sample image.** YOLO found 4 people and 1 bus. Inference took about 98 ms on the Pi 5. The person on the far left was only half visible and got a much lower score (0.56) than the others (0.84 to 0.86).

**Test on my own photo.** I took a photo with `snap.py` while holding my Keroro plush. YOLO detected me but not the plush.

<img src="images/bus_predict.png" alt="YOLO on bus.jpg" width="45%"> <img src="images/snap_predict.png" alt="YOLO on my snapshot" width="45%">

**Ollama and Qwen.** Both models downloaded successfully. Ollama warned that no GPU was found, so it runs on the CPU only.

<img src="images/ollama_list.png" alt="ollama list" width="500">

## Part B. See: recognize people in real time with YOLO

I ran `yolo_person.py` through VNC and tried different objects and poses in front of the webcam.

<!-- Add your video link or screenshots here -->

**Three situations where the detector got it wrong**

**1. A photo of a person on my phone was detected as a real person (0.80).**
I held up my phone with a photo of Donald Trump. YOLO drew a person box around the screen with 0.80 confidence. I think this happens because YOLO only looks at shapes and colors in a flat image. It has no depth information, so it can't tell a real person from a picture of one. In a system that counts people in a room, this would add a person who isn't there.

<img src="images/yolo_trump_photo.png" alt="Photo of a person on my phone" width="400">

**2. A photo of a teddy bear was labeled as a person (0.51).**
I showed a photo of a teddy bear on my phone. YOLO labeled it "person" with 0.51 confidence. A teddy bear has a head, arms and a body, so its outline looks a bit like a person's. The low score shows the model wasn't sure. A higher confidence threshold (for example `--conf 0.7`) would filter out this kind of mistake, but it might also make the system miss real people.

<img src="images/yolo_teddy_photo.png" alt="Teddy bear photo labeled as person" width="400">

**3. My own confidence dropped when part of my body was hidden.**
With nothing in my hands, YOLO detected me at 0.91. When I held up my phone, my score dropped to 0.58, because my arm and the phone covered part of my face and upper body. When I stretched out my arm to hold the plush and my face went out of the frame, the box only covered half of my body. The model can only judge what it sees, so occlusion and the edges of the frame both make it less sure.

<img src="images/yolo_arm_out.png" alt="Arm stretched out, face out of frame" width="400">

**Other observation:** My Keroro plush was never detected. This is correct, since it's not a person. Even with `--all`, a cartoon frog isn't one of YOLO's 80 classes.

<!-- If you test the flags (--conf 0.2, --conf 0.7, --all, --headless), add what you saw here. -->

## Part C. See more: hands and pose with MediaPipe

<details>
<summary>Instructions</summary>

The demo plays a looping audio file, so make the helper script executable first:

```
chmod +x loop_audio.sh
python hand_pose.py
```

Run it through VNC (it will not work over ssh). Try 1) pinching for percentage control (the thumb-to-index distance sets the volume), and 2) "Quiet Coyote" (the gesture mutes it). A hand is 21 landmark coordinates, and an event is a rule you write over them, the same job as `interpret()` in Part E.

</details>

I ran `hand_pose.py` through VNC with the USB speaker plugged in. The thumb-to-index distance controls the volume.

**What I observed**

**1. Normal use.** With my hand close to the camera, closing my fingers set the volume to 0%, and opening them as wide as I could reached about 60%.

<img src="images/hand_pose_close_0.png" alt="Fingers closed, 0%" width="45%"> <img src="images/hand_pose_close_60.png" alt="Fingers open, about 60%" width="45%">

**2. Distance.** The hand was still tracked when it was far from the camera. But even with my fingers fully open, the volume only went up to about 6%. The script measures the distance between the two fingertips in pixels, so a hand that looks smaller on screen gives a smaller number. A better rule would divide the pinch distance by the size of the hand (for example, the wrist-to-palm length), so the same gesture means the same thing at any distance.

<img src="images/hand_pose_far.png" alt="Hand far from the camera, about 6%" width="400">

**3. Two hands.** When both hands were in the frame, the control stopped working as expected, and the landmarks jumped to the hand farther from the camera. The script follows only one hand, and it doesn't always pick the one I'm using. In a shared room, this means the device can't tell who is giving the command.

<img src="images/hand_pose_two_hands.png" alt="Two hands, landmarks on the farther hand" width="400">

**4. Back of the hand.** With the back of my hand facing the camera, closed fingers still gave 0%, but fully open fingers only reached about 4%. From behind, the thumb and index finger overlap more in the 2D image, so the measured distance is much shorter.

<img src="images/hand_pose_back_0.png" alt="Back of the hand, fingers closed, 0%" width="45%"> <img src="images/hand_pose_back_4.png" alt="Back of the hand, fingers open, about 4%" width="45%">

**How I might use pose tracking in my observant system**

Hand gestures are a good fit for the bedtime hand-off in Part 2, because the person can talk to the device without touching anything or speaking in a quiet room. A Quiet Coyote at the camera could mean "start the night shift," and the device could answer by dimming its screen. A pinch could set one number before bed, such as how sensitive the night watch should be.

My tests also showed what I need to design around. The gesture should be made close to the camera with the palm facing it, so I would show a small guide on the screen. If two hands appear, the device should ignore the command and wait, instead of guessing whose hand it is.

## Part D. Reason: run Qwen on the Pi with Ollama

<details>
<summary>Instructions</summary>

```
ollama run qwen3.5:0.8b
>>> /set nothink
>>> Tell me a bit about the history of Roosevelt Island. Three sentences.
>>> /bye
```

Ground truth: it was Blackwell's Island, then Welfare Island, got its current name in 1973, and Cornell Tech opened on it in 2017. Count how many of the model's facts are true.

```
python ask_qwen.py "Tell me a bit about the history of Roosevelt Island."
python ask_qwen.py --model qwen3:0.6b "Tell me a bit about the history of Roosevelt Island."
python ask_qwen.py --image snap.jpg "What is in this photo?"
```

Useful flags: `--think` turns the reasoning trace back on, `--json` asks for a JSON answer, `--image` sends a photo, `--max-tokens` changes the length limit.

</details>

***Ask Qwen three questions to answer. Paste the questions, the answers and how long each took. Try one of them with `qwen3:0.6b` and compare.***

**Question 1: celebrity facts, two models compared**

> When did Taylor Swift get married, and when did she break up with Harry Styles?

**Ground truth:** Taylor Swift married Travis Kelce on July 3, 2026, at Madison Square Garden. She and Harry Styles broke up in early January 2013.

| Model | Answer | Time |
|---|---|---|
| `qwen3.5:0.8b` | "Taylor Swift got married on July 15, 2016, while Harry Styles was still a child. They broke up in March 2024 after a long period of infidelity." | 6.6 s |
| `qwen3:0.6b` | "Taylor Swift got married in 2016, and she broke up with Harry Styles in 2019." | 8.8 s |

Both models got every fact wrong, and both answered with full confidence. The 0.8b model made up a precise date (July 15, 2016) and even a reason for the breakup ("infidelity"). It also said Harry Styles was "still a child" in 2016, but he was in his twenties. The 0.6b model gave shorter, vaguer answers, but they were still wrong. Neither model said "I don't know," even though the wedding probably happened after their training data ends. The smaller model was a bit slower here, probably because it had to load into memory first.

**Question 2: describe a photo**

> What is in this photo? (`--image snap.jpg`, the photo of me holding my Keroro plush from Part A)

| Model | Answer | Time |
|---|---|---|
| `qwen3.5:0.8b` | "A person holding a plush toy of a green turtle-like creature with closed eyes and blonde hair." | 31.9 s |

This answer is partly right. It saw a person holding a green plush toy, which is correct. Keroro is a frog, not a turtle, but "green turtle-like creature" is close. The "blonde hair" is probably Keroro's yellow hat. Looking at an image took about five times longer than a text question.

**Question 3: a wrong premise, thinking on vs. off**

> When did World War III happen?

I asked this in `ollama run qwen3.5:0.8b --verbose`, first with thinking on and then with `/set nothink`.

| Mode | Total time | Tokens generated | Speed |
|---|---|---|---|
| Thinking on | 69.7 s | 390 | 5.6 tokens/s |
| Thinking off | 36.9 s | 208 | 6.0 tokens/s |

With thinking on, the model mostly got it right. It said World War III never happened, but one line contradicted this by saying "World Wars I-III" are in the historical record. With thinking off, it was twice as fast, but the answer fell apart. It said there was no World War III, then said there were "three World Wars historically recorded," gave the Civil War as an example of a "Third World War," and said "the actual conflicts occurred during the Second World War." The speed per token was about the same, so the time saved came only from skipping the reasoning. (The second run also carried the first conversation as context, so this is not a perfectly fair comparison.)

**What I learned**

A 0.8B model sounds confident even when it is completely wrong. It invents dates and details instead of saying it doesn't know, and it can agree with a false premise. In my observant system, I shouldn't ask Qwen for facts it has to remember. I should give it the data (like a night log) and keep its job narrow, so it only summarizes what is actually there.

## Part E. Connect: from YOLO readings to Qwen reasoning

<details>
<summary>Instructions</summary>

`yolo_to_qwen.py` is the whole pipeline: `interpret(r)` turns one YOLO result into a reading in words, `watch(seconds)` logs a reading only when it changes, and `ask(log, question)` sends the log and a question to Qwen. Run it through VNC, or add `--headless` over ssh.

```
python yolo_to_qwen.py
```

It watches for a minute, saves the log to `watch_log.txt`, and asks Qwen the default question.

</details>

I ran `yolo_to_qwen.py` through VNC. During the minute, I walked over to the right side of the room, stayed there for a bit, and walked back.

<img src="images/yolo_to_qwen_camera.png" alt="Camera view while yolo_to_qwen.py was watching" width="500">

**The log**

```
22:56:46 nobody
22:57:00 1 person(s) at right/close
22:57:01 1 person(s) at center/close
22:57:06 1 person(s) at right/close
22:57:10 1 person(s) at center/close
22:57:11 1 person(s) at left/close
22:57:13 nobody
22:57:19 1 person(s) at left/close
22:57:21 1 person(s) at left/far
22:57:22 nobody
22:57:29 1 person(s) at left/close
22:57:31 1 person(s) at center/close
22:57:31 1 person(s) at right/close
22:57:35 nobody
```

**Qwen's answer (15.0 s)**

> There are 1 person(s) present throughout the entire 60-second period. They were all located within the right or center areas of the room and were consistently moving around between left and center positions. No significant change in activity occurred; everyone remained stationary relative to each other for most of the time, though some individuals moved slightly closer to their respective locations.

**Where the pipeline broke**

*The interpreter.* My movement was simple, but the log has 14 lines. Almost every reading is "close," because the webcam sits near me and my box is usually taller than 60% of the frame, so the close/far rule tells me almost nothing in this setup. Walking across the frame passes through all three thirds in a second or two (two readings at 22:57:31), so one walk becomes several lines. The short "nobody" gaps happen when I step out of the frame or my body is cut off at the edge. The log records every small change, not the event I care about.

*The reasoner.* Qwen's summary doesn't match the log. It says one person was there "throughout the entire 60-second period," but the log shows "nobody" four times. It says the person stayed in the right or center, then says they moved between left and center. It says "no significant change in activity" and "everyone remained stationary," but the log is full of movement. It also talks about "everyone" and "some individuals," even though there was only one person. The model didn't invent new events this time, but it couldn't read a noisy log correctly.

**What I would change**

* Make the interpreter coarser: only log "someone arrived" and "someone left," and ignore gaps shorter than a few seconds.
* Adjust the close/far threshold to fit where the camera actually sits.
* Ask Qwen a narrow question, like "How many times did someone enter?", instead of an open summary. Or count the events in code and only ask Qwen to put the numbers into a sentence.

---

# Part 2: Overnight Sensation

<details>
<summary>Instructions</summary>

Design a night-time interaction. The person meets the device twice: **before bed** (hand the night over) and **when awake** (the device gives something back). In between, the Part E pipeline runs on its own.

* **Sense all night:** the Pi watches, listens or measures and keeps a log.
* **Process all night:** you already have data and the Pi needs hours to get through it.

House rules: sample rather than stream (one look every 5 to 30 seconds), and keep everything on the Pi. Tell the people you live with, point the camera at things rather than at beds, and delete snapshots after the demo.

</details>

## Part F. Storyboard your night-time interaction

***Insert ideas, sketches, Verplank diagrams and storyboards for your night-time interaction here.***

**Ideas I considered**

1. **Movement alarm.** The camera watches the bed and tracks how the sleeper moves from left to right and how long they stay in bed, and the alarm goes off based on that. It fits the Part E pipeline well, since interpret() already reports left, center and right. I dropped it because it needs a camera pointed at a bed, which breaks the house rules, and a camera watching someone sleep all night felt too invasive.  
<img src="images/movement_alarm.jpg" width="500">

2. **Night palette.** A color sensor records the light in the room all night, and the morning report is a strip of color: the desk lamp, a phone screen at 1 a.m., the blue hour before sunrise. I liked how it handles a quiet night (one flat color is still an honest answer), but it also skips the YOLO part of the pipeline.

3. **Door Watch (chosen).** The Pi sits by a front door all night and notes when someone comes close, how long they stay, and takes one photo if they stay. In the morning it tells you who stopped by while you were asleep. This one uses the whole Part E pipeline (camera, YOLO, `interpret()`, a log, Qwen), and the camera points at a door, not a bed.  
<img src="images/Door_watch_and_Night_palette.jpg" width="500">

**Where it runs.** My classmate lives alone in a studio, so the device watches the hallway outside her door. She knows about it and agreed. The camera faces the door and the space in front of it. For the real night, I ran it in my own kitchen instead (see Part H).

<img src="images/door_setup.png" alt="Door Watch set up outside the door" width="300">

**Verplank diagram**

| | |
|---|---|
| **Idea** | A night guard for your door that reports in the morning instead of waking you up. |
| **Metaphor** | A doorman who works the night shift. You hand over the night before bed, and he tells you in the morning who came by. |
| **Model** | Every 5 seconds the camera takes one look. A visit starts when a person appears and ends when nobody has been seen for about 10 seconds. Visits of 10 seconds or more get one photo. |
| **Display** | The Mini PiTFT (status at night, report in the morning) and a timeline image with photos. |
| **Error** | A quick walk-past can fall between two looks and be missed. Two visits close together merge into one. The person pressing the button in the morning can look like a visitor. YOLO counts a photo of a person as a person (Part B). |
| **Scenario** | Before bed, you make a quiet coyote at the camera. In the morning, you press a button and see "2 visits, longest 1m40s at 02:14", then flip through the photos. |
| **Task** | Arm the device. Read the report. Look at who stayed. |
| **Control** | A hand gesture to start, button A to stop and show the report, button B to flip photos. |

**First storyboard**

<img src="images/storyboard2-1.jpg" alt="First storyboard" width="800">

1. **10 PM.** Before going to bed, she mounts the camera on the outside of her door.
2. **3 AM.** She is asleep. Someone walks up to the door.
3. **3 AM.** The camera flashes and takes a photo of the person standing there.
4. **7 AM.** The Pi sends an alert to her laptop over Wi-Fi, and she sees it while she is having breakfast at her desk.
5. **Later.** She recognizes the person from the photo and confronts them.

In this version the device is only a camera. Nothing happens between the person and the device before bed, and the morning report shows up on a laptop instead of on the device itself.

## Prep for Part 2

**Feedback on my ideas:**

Feedback from Rohil Saraf (rs2685): The main concern with the original idea is that it's unclear what the device actually does. Frame 3 of the storyboard just shows a light, but it is not obvious to the reader that it is taking a photo. Slide 4 also makes it seem like the device is uploading the photo to the cloud and the person is then viewing the images from a computer. They also mentioned what would happen if someone just passes in front of the camera briefly, to which we explained that the device won't take a photo.

Feedback from Jason Sebastian (js3685): It isn't clear why the person is angry at Slide 5. Also confused about the quiet coyote gesture because Slide 1 makes it look like the user pressed a switch to start the device. They were also wondering if the device uploads photo via WiFi.

Both of them were confused by the same few things, and each one led to a change in the revised storyboard below:

* **It wasn't clear that the device takes a photo.** In the revised version, panel 4 says "CLICK!" and the visitor reacts to it.
* **It looked like the photo was uploaded to the cloud and viewed on a computer.** Now the report and the photo appear on the device itself, and nothing leaves the Pi.
* **It looked like the user pressed a switch to start it.** Now panel 1 shows the Quiet Coyote gesture.
* **It wasn't clear why she was angry at the end.** We removed that panel. The story ends when she reads the report.
* **What if someone only walks past?** The device only takes a photo when someone stays for 10 seconds or more, so people passing by aren't photographed.

***Post your revised storyboard here.***

<img src="images/storyboard2-2.jpg" alt="Revised storyboard" width="800">

1. **10 PM.** Before bed, she makes a Quiet Coyote at the device on her door. This is the hand-off: the device takes over the night.
2. **11 PM.** She is asleep. The device sits by the door with its screen off, taking one look every 5 seconds.
3. **3 AM.** A stranger in a mask stops at her door and looks around.
4. **3 AM. CLICK!** The stranger has stayed longer than 10 seconds, so the device takes one photo. He notices and is startled.
5. **3 AM.** He sits down outside the door. She keeps sleeping. The device logs how long he stays, but it doesn't wake her up.
6. **7 AM.** She presses A on the device, and the screen reads "1 visit at 3 AM" with his photo. "OMG!"

**What changed from the first storyboard**

* **A real hand-off before bed.** In the first version she only set up a camera. Now the night starts with a gesture, so the person and the device meet twice, as the assignment asks.
* **The report lives on the device.** The first version sent the photo to a laptop. Now the summary and the photo appear on the Mini PiTFT, and everything stays on the Pi.
* **A morning report, not a night alarm.** The device doesn't wake her when someone comes. It keeps a log and tells her what happened when she is ready to hear it.
* **Stay time, not just a photo.** Panel 5 is new: what matters is not only who came, but how long they stayed. Someone who walks past isn't photographed. Someone who sits outside the door for minutes is.
* **No confrontation panel.** The device tells her what happened. What she does about it is her decision, so the story ends when she reads the report.

## Part G. Design the interaction before

***Describe the interaction before.***

The hand-off is a gesture, not a command. When the script starts, the PiTFT says **"Night shift: show a quiet coyote."** The person makes a Quiet Coyote at the camera (index and pinky up as ears, middle and ring finger touching the thumb as a snout) and holds it for two seconds. The screen shows "Hold it..." with dots filling up, then **"Armed, screen off in 10"**. The 10 seconds are there so the person can close the door and walk away before the watch starts. Then the backlight turns off so the hallway stays dark.

<img src="images/door_coyote.png" alt="Making a Quiet Coyote at the camera to arm the device" width="450">

What the PiTFT shows during the hand-off:

| Step | Screen |
|---|---|
| Waiting | "Night shift" with "show a quiet coyote / to start watching" underneath |
| Gesture seen | "Hold it..." with green dots that fill up over two seconds |
| Armed | "Armed" with "screen off in 10", counting down to 1 |
| Watching | Black, with the backlight off |

I picked the Quiet Coyote because it already means "quiet now" in classrooms, and handing over the night is a way of saying "I'm going to sleep, you take it from here." It also works without touching anything or speaking in a quiet hallway at night.

What I learned in Part C shaped the details:

* **One hand only.** In Part C the landmarks jumped to the wrong hand when two hands were in view. Here, if MediaPipe sees two hands, the device waits instead of guessing whose command it is.
* **A little forgiveness.** On my first tries the hold timer restarted every time MediaPipe lost the hand for a single frame. Now the gesture has to be gone for half a second before the timer resets.
* **An easier pinky.** When the ring finger bends to touch the thumb, the pinky gets pulled down with it. My debug output showed `pinky_up: False` almost every time, even though the snout was fine (`middle_to_thumb: 0.03`). I changed the rule so the pinky tip only needs to be above its base knuckle, not its middle joint. After that it worked with one hand.
* **A backup.** If the gesture doesn't work, button A also arms the device.

## Part H. Incorporate the observant system

<details>
<summary>Instructions</summary>

```
tmux new -s night
python overnight.py
```

Fake a night first (30 seconds of walking around), then run `morning.py`. When that looks right, run a real night. Stop with Ctrl-C in the morning.

</details>

***Screenshots or videos of the running system.***

I built my own version of the overnight and morning scripts on top of `yolo_to_qwen.py`, so the pipeline is the same as Part E:

| Part E | Door Watch (`door_watch.py`) |
|---|---|
| camera + YOLO | one look every 5 seconds, `person` class only, confidence 0.6 or higher |
| `interpret()` | only "someone" or "nobody". No left/right, no close/far |
| `watch()` logs changes | logs an arrival, a photo, and a departure with how long they stayed |
| `ask()` sends the log to Qwen | moved to the morning, with a much narrower job (Part I) |

```
tmux new -s night
python door_watch.py
```

**Design decisions, based on what broke in Part E**

* **A coarser interpreter.** In Part E one walk across the room became 14 log lines. At a door the only question is whether someone is there, so that's all `interpret()` reports.
* **Leaving means gone for a while.** A visit ends only after two empty looks in a row (about 10 seconds). Otherwise one person turning around would count as two visits.
* **One photo, only if they stay.** A photo is taken when someone has been there for 10 seconds. People walking past aren't photographed.
* **A higher confidence threshold.** In Part B, a photo of a teddy bear passed as a person at 0.51. I start at 0.6.
* **Save after every visit.** If the script crashes at 4 a.m., the visits before that are still on disk, and the morning report says the watch stopped early.

**Fake nights**

<img src="images/door_visitor.png" alt="Standing in front of the door during a fake night" width="450">

During a fake night I stood in front of the door for longer than 10 seconds so the Pi would take a photo. The log from that run:

```
22:02:57 armed, one look every 5s
22:02:58 someone arrived (0.89)
22:03:12 still here after 14s, took snapshots/20261028_220312.jpg
22:03:37 left after 39s
22:03:41 stopped by button A
```

**Problems I found while testing**

* **I was counted as a visitor.** When I walked up to press A in the morning, YOLO saw me and logged `someone arrived (0.82)` followed by `left after 0s`. The device only knows that someone is there, not who. Now, if a visit started less than 15 seconds before button A is pressed, it's treated as the owner and dropped (`ignored the last few seconds: that was you pressing A`).
* **Quick visits can be missed or merged.** With one look every 5 seconds, someone who walks past in 2 or 3 seconds can fall between two looks. And when I walked past and came back within 10 seconds, the two became one 39-second visit. This is the cost of sampling instead of streaming. For a door, I think it's acceptable: the people who matter are the ones who stop.
* **The screen was busy.** The Pi already runs a service that shows its IP on the PiTFT at boot (`piscreen.service`), so my script got `Device or resource busy`. I stop that service before a night with `sudo systemctl stop piscreen.service`.

**A real night**

I ran Door Watch for one real night at home, from 01:25 to 09:31 on November 1. I set it up in our kitchen, and I armed it with the Quiet Coyote before going to bed. In the morning I pressed A to end the night.
<img src="images/night_report_real.png" alt="Timeline report" width="700">



```
01:25:14 armed, one look every 5s
01:25:19 someone arrived (0.92)
01:25:19 left after 0s
...
01:58:38 someone arrived (0.80)
01:58:48 still here after 10s, took snapshots/20261101_015848.jpg
01:59:18 left after 40s
01:59:33 someone arrived (0.86)
01:59:43 still here after 10s, took snapshots/20261101_015943.jpg
01:04:40 left after -3293s
01:04:55 someone arrived (0.86)
01:04:55 left after 0s
01:25:21 someone arrived (0.86)
...
01:37:24 someone arrived (0.87)
01:37:34 still here after 10s, took snapshots/20261101_013734.jpg
01:41:30 left after 246s
07:27:55 someone arrived (0.61)
07:27:55 left after 0s
07:28:10 someone arrived (0.63)
07:28:10 left after 0s
08:37:59 someone arrived (0.84)
...
09:24:10 left after 15s
09:31:03 someone arrived (0.89)
09:31:07 ignored the last few seconds: that was you pressing A
09:31:07 stopped by button A
```

The device logged 22 visits and took 16 photos. Here is what I learned from it.

<img src="images/realnight_report.JPG" alt="The report on the screen in the morning" width="450">
<img src="images/realnight_photo.JPG" alt="The snapshot of the visitor." width="450">

**1. Almost every "visitor" was us.** The kitchen turned out to be the busiest spot in the apartment at night. My roommate and I kept getting up for water, so most of the 22 visits are the two of us. The first ones at 01:25 are me, still in front of the camera after making the gesture. This is the same problem I found when I pressed A during testing: YOLO only knows that someone is there, not who it is. For a real door watch, the camera has to face a door that only visitors use. A spot that the people who live there walk past all night makes the log useless as a security record.

**2. The clocks went back in the middle of the night.** Daylight saving time ended at 2:00 a.m. on November 1, and the clock went back to 1:00. So the hour from 1:00 to 2:00 happened twice. My script logged plain local times, so when someone arrived at 01:59:33 (daylight time) and left at 01:04:40 (standard time), it calculated that they stayed **−3293 seconds**. Everything from `01:04:40` to `01:41:30` in the log is the second 1 a.m., even though it looks like it happened before 01:59. The night also looked one hour shorter than it really was (8 hours instead of 9).

This changed the morning report. When I pressed A, the PiTFT showed **"22 visits, longest 4m06s at 01:37."** The broken visit couldn't be the longest, because its stay was negative, so the script counted it as someone who just passed by. In reality, the 01:59 visit was the longest, at about 5 minutes. A short fake night could never have shown this bug. I only found it because I ran a real night, and it happened to be the one night of the year when 1 a.m. comes twice.

I decided to leave the code as it is and keep this night's log as a record of the bug. If I fixed it, the fix would be to save every time with its UTC offset (for example `01:59:33-04:00` and `01:04:40-05:00`), or as a timestamp that doesn't depend on the local clock at all. Then the stay would come out as 5m07s, and the timeline would show the full 9 hours. It's a good reminder that "how long did they stay" is a harder question than it looks, because the wall clock isn't always a straight line.

**3. Two detections right at the threshold.** At 07:27 and 07:28 there were two one-look visits at 0.61 and 0.63 confidence, just above my 0.6 threshold. They didn't last long enough to get a photo, so I can't say for sure what they were. They happened around sunrise, so the changing light may have made something look like a person. This is the trade-off I noted in Part B: a higher threshold would drop these, but it might also miss real people.

**4. The morning hand-off worked.** At 09:31 I walked up and pressed A. The script saw me at 09:31:03 and dropped that visit (`ignored the last few seconds: that was you pressing A`), then showed the report on the PiTFT.

## Part I. Design the interaction after

<details>
<summary>Instructions</summary>

```
python morning.py
```

Show the report on the Mini PiTFT, read it aloud with Piper, plot a timeline, or use `--json` to move a servo. Make sure it is honest about a night in which nothing happened.

</details>

In the morning the person presses **button A**. The watch stops, and the PiTFT goes from "Good morning, reading the night..." to the report:

* **Summary screen:** the number of visits and the longest stop, for example "1 visit, longest 39s at 22:02".
* **Button B** flips through the photos, each labeled with the time and how long the person stayed. **Button A** goes back to the summary.

<img src="images/door_pitft_report.png" alt="Summary screen on the PiTFT" width="45%"> <img src="images/door_pitft_photo.png" alt="A snapshot shown on the PiTFT after pressing B" width="45%">

*Left: the summary screen after pressing A. Right: the snapshot after pressing B, with the time and how long the person stayed in a black bar at the bottom.*

* `door_morning.py` also saves `night_report.png`: a timeline of the whole night where people who passed by are blue marks, people who stayed are orange marks, and the photos sit above the times they were taken.

<img src="images/night_report.png" alt="Timeline report" width="700">

**What Qwen does, and what it doesn't**

Parts D and E showed me that a 0.8B model invents details and can't read a noisy log. So the counting happens in code: number of visits, how many passed by or stayed, the longest stop, the time window. Qwen only gets those facts and is asked to put them into one or two sentences. Its answer is used only if it passes a check. Otherwise the report uses the sentence written by the code.

The check grew every time Qwen found a new way to be wrong. These are real answers from my test runs:

| Qwen said | Facts | Problem |
|---|---|---|
| "The camera recorded two visits during the hour." | 2 visits in 1 minute, longest 20s at 21:16 | No numbers were wrong, but it dropped the important parts and made up "during the hour." |
| "Two visits occurred at 21:40. The longest visit lasted 39 seconds." | the second visit was at 21:41 | It made both visits sound like they happened at 21:40. |
| "Two visits were made at 22:02." | 1 visit | Wrong count. My first check missed it, because it found a "1" inside "21:52". |

So now Qwen's sentence is rejected if it adds a time or number that isn't in the facts, if it leaves out the number of visits or the time of the longest one, if it uses a different count word, or if it puts the longest visit's time in a sentence that doesn't say "longest." In practice the code's own sentence ends up in the report more often than Qwen's, and I think that's the right outcome for a security device.

**A night when nothing happens**

If nobody comes, the PiTFT says **"All quiet: no one came to the door"** with the hours it watched, and the report says *"No one came to the door last night (23:42 to 07:31)."* The timeline is drawn empty. Qwen is also rejected here if it doesn't clearly say that nobody came, so "It was a calm night" doesn't pass. If the script stopped early, the report says so, instead of pretending the rest of the night was quiet.

**Deleting the photos**

After the demo, all snapshots are deleted on the Pi:

```
python door_morning.py --delete
```

## Final documentation

***Include a short video of the whole system***

[Video of the whole system (Google Drive)](https://drive.google.com/file/d/1xinoOYyEnypRQvebepxpyevP9NqrYxc-/view?usp=drive_link)

The video follows one fake night from start to finish: making the Quiet Coyote to arm the device, the countdown and the screen going dark, a person stopping at the door long enough to be photographed, pressing A to end the night, and pressing B to see the photo on the PiTFT.
