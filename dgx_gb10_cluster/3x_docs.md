20261105
setup 2x nodes w/ gemini copilot a few months ago ... now after pulling my tooth out with a rusty set of pliers... i add node 3
  and miaai-lab has a recipe for 3 nodes deepseek41flash_1017.  This time in agy console and root access to everything
  on the box and ssh to the nodes i fire it up ... burns 50% of my weekly quota on gemini4flashargon-high ... 6 hours later it is running...
  it finds my huggingface cert on one of the boxes (login was added after the anthropic hack) and it takes hours to download 436g
  and i thought it was broken when i went to bed .. but next morning 121g vram on all the nodes and dsh finds the model serve. (http not https)

  normally dumping the chat is useless and unreadable but it did 2 things different.  Because i have only 2cables ($40 each by the way)
  local-link doesn't have an ip on startup  .. i did the ip setup manually for the 2x setup.... i sort of plugged in the
  cables to make the daisy chain and i made the center node the master w/ the 2 nics and the workers at the end and no i didn't read the nohubrouterreadem on miaai-lab either   

  I think the word dev in devops is to pretend the buggy mess is part of the overall agile plan (billable at $100/hr)... what is different is gemini4 found the bugs fixed the bugs read the logs i didn't read and would never read really, restarted (deleted the buggy netplan created by nvidia-sync cluster setup) and tested updated firmware and tested and i was sound asleep...
