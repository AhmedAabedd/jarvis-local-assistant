IDEAS:

## THE KILLER : Reorganize all the code to use langraph capabilities instead of manul ones like tools use langraph annotation for tools instead of creating schema manually (and other things that are stable and implemeted in langraph framework so we reduce complexity of the code and use already used ones instead of implementing things manually...)

## 1- wrap open/close browser and open youtube tools into a new built-in sub-agent that we can call it browser or something like that

## 2- add new tool to the orchestrator to append to file: its like adding something to a file without modifying its content) adding new content at the end of the file

## 3- Make it capable of creating subagents for subagents not only for orchestrator mounir.

## 4- Make the application capable of creating multpile workflows and use each one of them separatly (for example create a workflow  of report generation(orchestrator -> web -> validator -> file generator -> validator -> orchestrator).

## 5- Add new feature to make a new node (currrently we have subagent node only) that can be another workflow from the system.

## 6- Add a sub-agent specialized in filing like pdf generation, excel etc...

## 7- Enhance the heartbeat by making it multi-records not only one , and make its config easier by adding a task (prompt) and select the sub-agents along with the tools like the existing approach and the task got read by mounir and mounir assign the task to the sub-agent like its normal request from the user.

## 8- Enhance the media agent by making him capable of reading images (already does) but also generates images , read videos , and that can be applied by selecting a model that has image and video input

## 9- Add mounir docs explaining how to set everything , like a roadmap (get started) until finishing configuring everything, and we can aslo add on start help when user first time open the app , its like setup guide (to set first model and set his profile) with an optional skip button if user already familiar with our app and can skip the help guide.

## 10- Integrate Gbrain(idk if we replace the knowledge subagent with it or not).

## 11- Add Flow visualization (live animated node diagram like n8n with lighting nodes and animated connection lines), we can use react flow to do it , but to use it we have first to move from vanilla js and normal html/css to react.

## 12- Add AI that can create subagent , the idea is the following:
        - to create new subagent , the user has to fill the form by adding the subagent name, the system prompt etc... and at the end select the MCP to be used by the subagent.
        - Due to some problems like spelling problems or brad system prompt generated manually , the user simply click on new button (generate with AI) , and describe what the agent does ( example: i want a subagent to do emails operations (read, send ...)) , and after confirming the AI will use a tool to list all the available MCP servers connected in the system , and if he finds something similar (like email MCP server) , he move to the next step , that is reading a skill of how to create the subagent (how to generate the name , how to generate the system prompt etc...) and all of that is done by a tool, at the end he select the MCP server he wants to use from the list above, and like that the subagent get created and innstantly appears in the flow schema without need to refrech.


some adjustments:
1- fix the models list to show all models in nodes like mounir and built-in sub-agents
2- enhance the notifications by showing only unread one and the others store them in a menu or button 
3- adding a button in the overview schema where u can add a sub-agent (like n8n), and a popup appear containing the exact same form of the create new sub-agent.
4- Fix the voice configuration by making them compatible dynamically not like the work done(especialy for me since im using groq or google cloud) , it has to be compatible for any cloud provider and also for local models , for both tts and stt .
5- Add new line to the github subagent text field(when to use) to tell « don't mention user name until the user ask explicitly »
6- Fix the gmail mcp server to not be specefic in the section of authentication because the purpose of our app is to be compatible and dynamic will all users all mcp servers etc so we cannot do specefic configurations.



