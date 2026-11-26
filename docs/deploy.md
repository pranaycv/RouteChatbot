# Put RouteChat online

The app runs on Streamlit Community Cloud from the `main` branch. It stays up when our computers are off. Each visitor enters their own API keys, so no key of ours is on the server and nobody pays for anyone else.

## Deploy

1. Merge the visitor-keys change into `main`.
2. Sign in at https://share.streamlit.io with the GitHub account that has admin rights on the repository. Authorize Streamlit if asked.
3. Click **Create app**, then **Yup, I have an app**.
4. Fill in: repository `pranaycv/RouteChatbot`, branch `main`, main file path `routechat/app.py`. Choose an app URL if you like.
5. Open **Advanced settings**. Choose Python **3.11**. In **Secrets**, paste this one line and nothing else. It is a setting, not a key.

   ```toml
   ROUTECHAT_KEYS = "visitor"
   ```

6. Save and deploy. The first start takes a few minutes.

If the line is added after the first start, use **Manage app**, the three dots, **Reboot app**.

## Check it

1. Open the link. There is an **Add your keys** button and the "Sample trip" mark, and the request box is off.
2. **Try the sample ride** works without keys.
3. Add your own keys and press **Check and use**. The mark goes away. Plan one trip.
4. Open the link in a private window. It must ask for keys again.
5. Try a trip from "my current location" once.

## Who can open it

The repository is public, so the app may start public. That is fine with visitor keys: a visitor without keys gets the sample ride only. For the team only, open the app's settings, then **Sharing**, choose **Only specific people can view this app**, and add the emails.

## What each person needs

| Key | Where |
|---|---|
| OpenRouter or OpenAI | https://openrouter.ai/settings/keys or https://platform.openai.com/api-keys |
| Mapbox token | https://console.mapbox.com/account/access-tokens/ |
| GraphHopper | https://graphhopper.com/dashboard/ |

Set a spending limit on the model key and make one only for this app, so it can be deleted afterwards. A Mapbox token limited to certain websites does not work, because the server makes the calls. GraphHopper's check uses one small search.

## How keys are handled

- They stay in the server's memory for one visit. A refresh or **Remove my keys** clears them.
- They are not written to the logs, the support tickets, or any file.
- A run sees its own visitor's keys only. In this mode the server's own keys are never used, even if someone sets them.
- They do pass through the server, and anyone who can push to `main` can change the code that handles them. Only enter keys if you trust the people with that access, and use limited keys.
- Never put keys in the repository, a pull request, or a chat.

## Good to know

- The app sleeps after 12 hours without visitors. Open it before a demo and wake it.
- A push to `main` updates the app. Tests should pass before a merge.
- After a merge to `main`, reboot the app: on https://share.streamlit.io, click the three dots next to the app, then **Reboot**. The update brings the new files, but the running app keeps the old version of files it has already loaded. A new file that needs something from the new version then fails, and a real request stops at "Checking your message". This happened on 26 November. The sample ride still works, so it does not show the problem.
- Logs and support tickets are in files on the server and can be lost on a restart. Requests are masked before they are logged. Each visitor sees only the tickets of their own visit.
- To stop the app, delete it on https://share.streamlit.io.
