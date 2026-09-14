const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/homework.js'), 'utf8');
const fragment = source.slice(source.indexOf('  function forLesson'), source.indexOf('  function authChanged'));

function controller(items) {
  const marked = [];
  const context = vm.createContext({items, active: item => item.course === 'Q1:ekeg8',
    resolveCourseKey: (subject, grade) => `${grade}:${subject.toLowerCase()}`,
    mark: (_, matches) => marked.push(...matches)});
  vm.runInContext(fragment, context);
  return {context, marked};
}
const assignment = changes => ({id:'one',course:'Q1:ekeg8',mode:'next',done:false,resolution:'resolved',
  due:{date:'2026-09-18',start:'09:10'},...changes});
const lesson = changes => ({grade:'Q1',subject:'EKEG8',date:'2026-09-18',start:'09:10',status:'normal',...changes});

test('lesson details include fixed-date homework only for its own course and day', () => {
  const {context} = controller([assignment({mode:'date',date:'2026-09-18'})]);
  assert.equal(context.forLesson(lesson()).length, 1);
  for (const change of [{grade:'EF'},{subject:'M L1'},{date:'2026-09-16'},{status:'entfaellt'}]) {
    assert.equal(context.forLesson(lesson(change)).length, 0);
  }
  assert.equal(context.forLesson(undefined).length, 0);
});

test('homework markers attach only to the exact selected grade, course, date and time', () => {
  const {context,marked} = controller([assignment()]);
  for (const change of [{grade:'EF'},{grade:'Q2'},{subject:'M L1'},{date:'2026-09-16'},{start:'10:20'},{status:'entfaellt'}]) {
    context.decorateLesson({},lesson(change));
  }
  assert.equal(marked.length,0);
  context.decorateLesson({},lesson());
  assert.equal(marked.length,1);
});

test('completed and unconfirmed homework never creates a lesson marker', () => {
  const {context,marked} = controller([assignment({done:true}),assignment({resolution:'pending'}),assignment({resolution:'unavailable'})]);
  context.decorateLesson({},lesson());
  assert.equal(marked.length,0);
});

test('fixed dates are marked on the day header even without a lesson', () => {
  const {context,marked} = controller([assignment({mode:'date',date:'2026-09-17'})]);
  context.decorateDay({},'2026-09-18');
  assert.equal(marked.length,0);
  context.decorateDay({},'2026-09-17');
  assert.equal(marked.length,1);
});

test('fixed-date markers also respect course selection and completion', () => {
  const {context,marked} = controller([assignment({mode:'date',date:'2026-09-17',course:'EF:ekeg8'}),assignment({mode:'date',date:'2026-09-17',done:true})]);
  context.decorateDay({},'2026-09-17');
  assert.equal(marked.length,0);
});
