const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const output=path.resolve(__dirname,'../../.tmp-test/desk-ui/visual-refresh');
fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const [width,height,scale,scheme='light'] of [[1440,1000,1],[1280,900,1.25],[1280,900,1.5],[1024,768,1],[640,720,1],[1440,1000,1,'dark'],[1024,768,1.25,'dark']]){
      const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:scale,colorScheme:scheme});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(pathToFileURL(path.resolve(__dirname,'../renderer/index.html')).href+'?lang=zh');
      await page.waitForSelector('.assistant-composer');
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),scheme,'Appearance follows the system scheme by default');
      await page.screenshot({path:path.join(output,`assistant-${width}-${scale}-${scheme}.png`)});
      const assistantControls=await page.locator('.theme-toggle').boundingBox();
      await page.locator('aside [data-session="call_01"]').click();
      if(await page.locator('.dock-heading').getAttribute('aria-expanded')==='false')await page.locator('.dock-heading').click();
      const before=await page.locator('.page-heading').boundingBox();
      const settingsBefore=await page.locator('.theme-toggle').boundingBox();
      await page.locator('.page-header').hover();
      const after=await page.locator('.page-heading').boundingBox();
      assert.deepEqual(after,before,'Header hover must not move the heading');
      assert.deepEqual(await page.locator('.theme-toggle').boundingBox(),settingsBefore,'Header controls must not resize on hover');
      assert.equal(settingsBefore.width,assistantControls.width);assert.equal(settingsBefore.height,assistantControls.height);
      const columnStarts=await page.locator('.worker-row').evaluateAll(rows=>rows.map(row=>[...row.children].map(el=>Math.round(el.getBoundingClientRect().x))));
      for(const row of columnStarts)assert.deepEqual(row,columnStarts[0],'Worker columns must line up across nesting and status');
      await page.locator('[data-worker="call_02"]').click();
      assert.ok(await page.locator('aside [data-session="call_01"] .session-state').count(),'A session with running workers is marked in the sidebar');
      assert.ok(await page.locator('.dock-counts .blue').count(),'The dock counts the running workers');
      assert.equal(await page.locator('.worker-expanded .terminal-output,.worker-expanded .process-entry').count(),0,'Inline expansion must not become a full log view');
      assert.equal(await page.locator('.worker-expanded .tool-capsule[open]').count(),0);
      assert.equal(await page.locator('.worker-expanded .compact-task[open]').count(),0);
      assert.ok(await page.locator('.worker-expanded .compact-peer').count()>0);
      assert.match(await page.locator('[data-worker="call_03"]').innerText(),/等你批准/);
      assert.match(await page.locator('[data-worker="call_04"]').innerText(),/等你回答/);
      assert.match(await page.locator('[data-worker="call_02"] .worker-duration').textContent(),/8:40/);
      const mainHeight=await page.locator('.conversation-scroll').evaluate(el=>el.clientHeight);
      assert.ok(mainHeight>=100,'Expanded dock must leave a usable conversation area');
      const contrast=await page.evaluate(()=>{
        const rgb=s=>(s.match(/[\d.]+/g)||[]).slice(0,3).map(Number);
        const luminance=values=>values.map(v=>{const n=v/255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
        return ['.worker-name','.worker-expanded-heading','.compact-time','.state.waiting','.state.running'].map(selector=>{
          const element=document.querySelector(selector);let parent=element,background=[255,255,255];
          while(parent){const value=getComputedStyle(parent).backgroundColor;if(value!=='rgba(0, 0, 0, 0)' && value!=='transparent'){background=rgb(value);break;}parent=parent.parentElement;}
          const a=luminance(rgb(getComputedStyle(element).color)),b=luminance(background);
          return {selector,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
        });
      });
      for(const item of contrast)assert.ok(item.ratio>=4.5,`${item.selector} contrast ${item.ratio}`);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(output,`expanded-${width}-${scale}-${scheme}.png`)});
      if(width===1280 && scale===1.5)await page.locator('.task-dock').screenshot({path:path.join(output,'task-dock.png')});
      if(width===1440 && scheme==="light"){
        await page.locator('.worker-expanded .tool-capsule>summary').first().click();
        await page.screenshot({path:path.join(output,'expanded-tools.png')});
        await page.locator('[data-detail="call_02"]').click();
        await page.screenshot({path:path.join(output,'worker-full.png')});
        await page.locator('[data-action="worker-back"]').click();
        await page.locator('aside [data-session="call_09"]').click();
        await page.screenshot({path:path.join(output,'completed.png')});
      }
      // The sidebar toggle must survive its own collapse (it lives in the titlebar).
      const toggle=page.locator('.titlebar-toggle');
      if(width>860){
        await toggle.click();
        assert.ok(await page.locator('.app.collapsed').count(),'Toggle collapses the sidebar');
        assert.ok(await toggle.isVisible(),'Collapsed sidebar keeps a visible toggle');
        await toggle.click();
        assert.equal(await page.locator('.app.collapsed').count(),0,'Toggle expands the sidebar again');
        assert.ok(await page.locator('aside .new-session .sidebar-text').isVisible(),'Expanded sidebar shows its labels again');
      }else if(width<=600){
        assert.ok(await toggle.isVisible(),'Narrow window keeps a toggle for the drawer');
        await toggle.click();
        assert.ok(await page.locator('.app.collapsed > aside').isVisible(),'Toggle opens the sidebar drawer');
        await toggle.click();
      }
      // AutoSwarm switch (demo: Claude Code and Codex have the Skill, Cursor's install failed).
      if(width<=600)await toggle.click();
      await page.locator('aside .new-session').click();
      await page.locator('#session-agent').click();await page.locator('[data-pick-agent="claude-code"]').click();
      assert.equal(await page.locator('#session-form .swarm-pill').getAttribute('aria-pressed'),'true','On by default once the Skill is there');
      await page.locator('#session-form .swarm-pill').click();
      assert.equal(await page.locator('#session-form .swarm-pill').getAttribute('aria-pressed'),'false','Off hides the Skill from this new conversation');
      assert.equal(await page.locator('.swarm-pop').count(),0,'A ready Skill never opens the popover');
      await page.locator('#session-agent').click();await page.locator('[data-pick-agent="cursor"]').click();
      assert.ok(await page.locator('#session-form .swarm-pill.is-locked').count(),'Cursor without the Skill is locked');
      await page.locator('#session-form .swarm-pill').click();
      const pop=await page.locator('.swarm-pop').boundingBox();
      assert.ok(pop.y>=0 && pop.y+pop.height<=height+1 && pop.x>=0 && pop.x+pop.width<=width+1,`Popover stays in the window: ${JSON.stringify(pop)}`);
      assert.match(await page.locator('.swarm-pop').innerText(),/自动安装没有成功[\s\S]*让 Agent 安装/);
      if(width===1440 && scheme==='light')await page.screenshot({path:path.join(output,'swarm-popover.png')});
      // Every agent can start with AutoSwarm; one that cannot hide a Skill per conversation keeps it on.
      await page.locator('#session-agent').click();await page.locator('[data-pick-agent="opencode"]').click();
      assert.equal(await page.locator('#session-form .swarm-pill').getAttribute('aria-pressed'),'true','OpenCode starts with AutoSwarm');
      assert.equal(await page.locator('#session-form .swarm-pill').getAttribute('aria-disabled'),'true','OpenCode cannot hide the Skill, so the switch stays on');
      // A long model list (every model of every provider configured in the agent) can be filtered.
      await page.locator('#session-model-chip').click();
      assert.equal(await page.locator('.model-menu [data-search]').count(),14);
      await page.locator('#model-filter').fill('qwen');
      assert.equal(await page.locator('.model-menu [data-search]:visible').count(),3,'Filtering keeps the matching models');
      const models=await page.locator('.model-menu').boundingBox();
      assert.ok(models.y>=0 && models.y+models.height<=height+1,`Model menu stays in the window: ${JSON.stringify(models)}`);
      if(width===1440)await page.screenshot({path:path.join(output,`model-filter-${scheme}.png`)});
      await page.locator('#model-filter').fill('no such model');
      assert.ok(await page.locator('.model-menu [data-filter-empty]').isVisible(),'Nothing matches: a note says so');
      await page.locator('#model-filter').fill('');
      await page.locator('[data-pick-model="volcengine/ark-code-latest"]').click();
      assert.equal(await page.locator('.model-menu .effort-option').count(),4,'The default model offers its levels');
      await page.keyboard.press('Escape');
      await page.locator('#session-agent').click();await page.locator('[data-pick-agent="codebuddy"]').click();
      await page.locator('#session-model-chip').click();
      assert.match(await page.locator('.model-menu .effort-row').innerText(),/Off[\s\S]*X-High/,"The agent's own names for its levels");
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#session-form .permission-chip').count(),0,'Agents without Desk permission modes keep their own settings');
      await page.locator('#session-agent').click();await page.locator('[data-pick-agent="cursor"]').click();
      // Permission modes: each agent's own names; the conversation's choice resets with the agent.
      const chip=()=>page.locator('#session-form .permission-chip span').innerText();
      assert.equal(await chip(),'Allowlist','Cursor starts in its Desk default');
      await page.locator('#session-agent').click();await page.locator('[data-pick-agent="codex"]').click();
      assert.equal(await chip(),'Ask for approval');
      await page.locator('#session-form .permission-chip').click();
      assert.deepEqual(await page.locator('.permission-menu .menu-text strong').allInnerTexts(),['Read only','Ask for approval','Approve for me','Full access']);
      const menu=await page.locator('.permission-menu').boundingBox();
      assert.ok(menu.y>=0 && menu.y+menu.height<=height+1 && menu.x>=0 && menu.x+menu.width<=width+1,`Permission menu stays in the window: ${JSON.stringify(menu)}`);
      if(width===1440 && scheme==='light')await page.screenshot({path:path.join(output,'permission-menu.png')});
      await page.locator('[data-pick-permission="full-access"]').click();
      assert.equal(await chip(),'Full access','This conversation only');
      await page.locator('#session-agent').click();await page.locator('[data-pick-agent="claude-code"]').click();
      assert.equal(await chip(),'Accept edits','A new agent starts from its own default');
      await page.locator('aside [data-session="call_01"]').click();
      assert.equal(await page.locator('.header-chips .permission-chip span').innerText(),'Accept edits','The session header shows its mode');
      // Running turn, as Codex shows it: interim text open, tools folded; the finished turn folded to its reply.
      assert.match(await page.locator('.live-steps .step-note').innerText(),/接着检查过期 token/);
      assert.equal(await page.locator('.live-steps details[open]').count(),0,'Tools in a running turn are folded');
      assert.equal(await page.locator('.turn .work-summary[open]').count(),0,'A finished turn is folded');
      assert.equal((await page.locator('.working-line > span:not(.state)').innerText()).trim(),'','The status line does not repeat the visible steps');
      assert.ok(await page.locator('[data-decision="approve"][data-scope="session"]').count(),'Allow for this session when the agent offers it');
      assert.equal(await page.locator('.chat-composer .swarm-pill').count(),0,'Running conversations keep what they started with');
      assert.deepEqual(errors,[]);
      console.log(`PASS visual ${width}x${height} @${scale} ${scheme}: stable chrome, aligned rows, compact details, no overflow, sidebar toggle, AutoSwarm switch, permission modes`);
      await page.close();
    }
    // Default language: English for Desk's own text; the preview keeps a choice made in its settings.
    for(const scheme of ['light','dark']){
      const page=await browser.newPage({viewport:{width:1280,height:900},colorScheme:scheme});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(pathToFileURL(path.resolve(__dirname,'../renderer/index.html')).href);
      await page.waitForSelector('.assistant-composer');
      assert.equal(await page.locator('aside .new-session .sidebar-text').innerText(),'New session');
      assert.match(await page.locator('.preview-banner').innerText(),/Browser preview/);
      assert.match(await page.locator('#assistant-input').getAttribute('placeholder'),/^Message the assistant/);
      await page.screenshot({path:path.join(output,`english-${scheme}.png`)});
      await page.locator('[data-action="settings"]').first().click();
      await page.locator('[data-language="zh"]').click();
      assert.equal(await page.locator('aside .new-session .sidebar-text').innerText(),'新会话');
      await page.reload();await page.waitForSelector('.assistant-composer');
      assert.equal(await page.locator('aside .new-session .sidebar-text').innerText(),'新会话','The preview remembers the language');
      assert.deepEqual(errors,[]);
      await page.close();
    }
    console.log('PASS visual: English by default, language switch kept by the preview');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
