import { describe, it, expect } from 'vitest';
import { NavigationSession } from './navigation';
describe('project work navigation',()=>{
 it('restores location scroll after leaving and across reload, with project isolation',()=>{
  sessionStorage.clear();const a=new NavigationSession('u:one',sessionStorage);
  a.visit('/datasets/commands?split=training','Commands');a.rememberScroll('/datasets/commands?split=training',387);
  a.visit('/finetuning/run','Run');a.visit('/datasets/commands?split=training','Commands');
  expect(a.scrollFor('/datasets/commands?split=training')).toBe(387);
  expect(new NavigationSession('u:one',sessionStorage).snapshot().entries).toHaveLength(2);
  expect(new NavigationSession('u:two',sessionStorage).snapshot().entries).toEqual([]);
 });
 it('closing a work tab preserves a resumable recent location and caps open tabs',()=>{
  const a=new NavigationSession('u:x');for(let i=0;i<12;i++)a.visit('/datasets/'+i,'Dataset '+i);
  expect(a.snapshot().entries).toHaveLength(8);a.close('/datasets/11');
  expect(a.snapshot().entries.some(e=>e.route==='/datasets/11')).toBe(false);
  expect(a.snapshot().recent[0].route).toBe('/datasets/11');
 });
 it('rejects malformed persisted entries and works when storage is denied',()=>{
  const a=new NavigationSession('u:x',{getItem:()=>'{"entries":[{"route":"https://evil.example","title":"bad"}]}',setItem:()=>{throw Error('denied')}});
  expect(a.snapshot().entries).toEqual([]);expect(()=>a.visit('/datasets','Datasets')).not.toThrow();
 });
});
