// Run with the Playwright browser tool's filename argument against local analysis 7.
async (page) => {
  const failures = [];
  const results = [];
  const check = (condition, message) => { if (!condition) failures.push(message); };
  for (const viewport of [{width:1440,height:900},{width:375,height:667},{width:812,height:375}]) {
    await page.setViewportSize(viewport);
    await page.goto('http://localhost:3000/analysis/7');
    await page.getByRole('heading', {name:'About the data'}).waitFor();
    const inspect = () => page.evaluate(() => {
      const header = document.querySelector('h2').parentElement;
      const content = header.nextElementSibling;
      const button = [...document.querySelectorAll('button')].find(e => e.textContent === 'Download HIN CSV');
      return {height:innerHeight,width:innerWidth,pageHeight:document.scrollingElement.scrollHeight,
        pageWidth:document.scrollingElement.scrollWidth,pageY:scrollY,top:content.scrollTop,
        maximum:content.scrollHeight-content.clientHeight,content:content.getBoundingClientRect().toJSON(),
        header:header.getBoundingClientRect().toJSON(),button:button.getBoundingClientRect().toJSON()};
    });
    const start = await inspect();
    check(start.pageHeight <= start.height, `${viewport.width}: page exceeds viewport`);
    await page.mouse.move(start.content.x + start.content.width/2, start.content.y + start.content.height/2);
    await page.mouse.wheel(0, 5000);
    await page.waitForFunction(() => { const c=document.querySelector('h2').parentElement.nextElementSibling;return c.scrollTop >= c.scrollHeight-c.clientHeight-1; });
    await page.mouse.wheel(0, 600);
    await page.waitForTimeout(200);
    const bottom = await inspect();
    check(bottom.pageY === 0, `${viewport.width}: wheel chains to page`);
    check(bottom.button.bottom <= bottom.height, `${viewport.width}: bottom download is clipped`);
    check(bottom.header.y === start.header.y, `${viewport.width}: panel header moves`);
    await page.getByRole('button', {name:'Close analysis details'}).click();
    await page.waitForTimeout(350);
    const closed = await page.evaluate(() => ({width:innerWidth,pageWidth:document.scrollingElement.scrollWidth}));
    check(closed.pageWidth <= closed.width, `${viewport.width}: closed drawer causes horizontal overflow`);
    await page.getByRole('button', {name:'Open analysis details'}).click();
    await page.waitForTimeout(350);
    const contentRegion = page.getByRole('region', {name:'Analysis details content'});
    await contentRegion.focus();
    await page.keyboard.press('Home');
    await page.waitForTimeout(200);
    const keyboardTop = await inspect();
    await page.keyboard.press('End');
    await page.waitForFunction(() => {const c=document.querySelector('h2').parentElement.nextElementSibling;return c.scrollTop >= c.scrollHeight-c.clientHeight-1;});
    const keyboardBottom = await inspect();
    check(keyboardTop.top === 0 && keyboardBottom.top > 0, `${viewport.width}: keyboard cannot scroll content`);
    await page.getByText('Technical details', {exact:true}).click();
    await contentRegion.focus();
    await page.keyboard.press('End');
    await page.waitForFunction(() => {const c=document.querySelector('h2').parentElement.nextElementSibling;return c.scrollTop >= c.scrollHeight-c.clientHeight-1;});
    const expanded = await inspect();
    check(expanded.button.bottom <= expanded.height, `${viewport.width}: expanded technical details clip downloads`);
    if (viewport.width < 768) {
      await page.locator('header button').click();
      const menuOpen = await inspect();
      check(menuOpen.pageHeight <= menuOpen.height, `${viewport.width}: mobile menu pushes analysis below viewport`);
      check(menuOpen.content.bottom <= menuOpen.height, `${viewport.width}: menu clips scrolling region`);
    }
    results.push({viewport, start, bottom, closed});
  }
  await page.setViewportSize({width:375,height:667});
  await page.goto('http://localhost:3000/');
  await page.getByRole('heading', {name:'Create Analysis'}).waitFor();
  await page.mouse.move(180,450);
  await page.mouse.wheel(0,2000);
  await page.waitForTimeout(200);
  const home = await page.evaluate(() => ({height:innerHeight,pageHeight:document.scrollingElement.scrollHeight,pageY:scrollY}));
  check(home.pageHeight <= home.height || home.pageY > 0, 'home page scrolling was disabled');
  if (failures.length) throw new Error(JSON.stringify({failures,results}));
  return {passed:true,results,home};
}
