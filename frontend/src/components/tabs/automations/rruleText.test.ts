import { describe, expect, it } from 'vitest'
import { rruleText } from './rruleText'

describe('rruleText', () => {
  it.each([
    ['FREQ=DAILY;BYHOUR=0,12;BYMINUTE=0', 'Every day at 12:00 AM and 12:00 PM'],
    ['FREQ=DAILY;BYHOUR=9;BYMINUTE=30', 'Every day at 9:30 AM'],
    ['FREQ=DAILY;BYHOUR=0,6,18', 'Every day at 12:00 AM, 6:00 AM and 6:00 PM'],
    ['FREQ=DAILY;INTERVAL=3', 'Every 3 days'],
    ['FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8', 'Weekdays at 8:00 AM'],
    [
      'FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=8;BYMINUTE=0',
      'Every Monday, Wednesday and Friday at 8:00 AM',
    ],
    ['FREQ=WEEKLY;INTERVAL=2;BYDAY=TU', 'Every 2 weeks on Tuesday'],
    ['FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=5', 'Every 2 months on the 5th'],
    ['FREQ=MONTHLY;BYMONTHDAY=1,15', 'Every month on the 1st and 15th'],
    ['FREQ=MONTHLY;BYMONTHDAY=-1;BYHOUR=17', 'Every month on the last day at 5:00 PM'],
    ['FREQ=MONTHLY;BYDAY=1MO;BYHOUR=9;BYMINUTE=0', 'Every month on the first Monday at 9:00 AM'],
    ['FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1', 'Every month on the last Friday'],
    ['FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=1', 'Every month on the first weekday'],
    ['FREQ=YEARLY;BYMONTH=4;BYMONTHDAY=15;BYHOUR=10', 'Every year on April 15 at 10:00 AM'],
    ['FREQ=HOURLY;INTERVAL=2', 'Every 2 hours'],
    ['FREQ=MINUTELY;INTERVAL=15', 'Every 15 minutes'],
  ])('reads %s', (rrule, text) => {
    expect(rruleText(rrule)).toBe(text)
  })

  // Each of these would read back wrong if a part were dropped, so none gets a sentence.
  it.each([
    'FREQ=DAILY;COUNT=3',
    'FREQ=DAILY;UNTIL=20270101T000000Z',
    'FREQ=DAILY;BYHOUR=9;BYMINUTE=0,30',
    'FREQ=DAILY;BYMINUTE=30',
    'FREQ=DAILY;BYHOUR=25',
    'FREQ=DAILY;BYDAY=MO',
    'FREQ=WEEKLY;BYDAY=1MO',
    'FREQ=MONTHLY;BYDAY=MO,WE;BYSETPOS=1',
    'FREQ=MONTHLY;BYDAY=FR',
    'FREQ=MONTHLY;BYSETPOS=2',
    'FREQ=MONTHLY;BYMONTHDAY=0',
    'FREQ=MONTHLY;BYMONTH=4',
    'FREQ=YEARLY',
    'FREQ=YEARLY;BYMONTH=4,5;BYMONTHDAY=1',
    'FREQ=HOURLY;BYMINUTE=30',
    'FREQ=DAILY;INTERVAL=0',
    'FREQ=DAILY;INTERVAL=2,3',
    'FREQ=SECONDLY',
    'BYHOUR=9',
    'RRULE:FREQ=DAILY',
    'FREQ=DAILY;FREQ=WEEKLY',
    'nonsense',
  ])('leaves %s to the raw rule', (rrule) => {
    expect(rruleText(rrule)).toBeNull()
  })
})
