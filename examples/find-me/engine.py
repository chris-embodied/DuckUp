"""Pure motion simulator. Degrees positive left; no actuator/network dependencies."""
from dataclasses import dataclass, asdict
import math

def wrap(v): return (v + 180) % 360 - 180
def approach(v, target, limit): return v + max(-limit, min(limit, target-v))

@dataclass
class Duck:
    state: str = 'idle'
    body: float = 0
    head: float = 0
    elapsed: float = 0
    lost: float = 0
    stable: float = 0
    scan: float = 0
    seen: bool = False
    message: str = '等待呼唤“小鸭小鸭”'

    def wake(self):
        self.state='search'; self.elapsed=0; self.scan=0; self.lost=0; self.stable=0
        self.message='我在，正在找你。'

    def tick(self, dt, bearing=None, ambiguous=False):
        dt=max(0, min(.2,dt)); self.seen=False
        if self.state=='idle': return asdict(self)
        if ambiguous:
            self.state='ambiguous'; self.message='看到多个人，暂不判断谁在叫我；请先单人测试。'
            self.stable=0
            return asdict(self)
        visible=bearing is not None and abs(wrap(bearing-self.body-self.head)) <= 38
        if visible:
            self.seen=True; self.lost=0; self.elapsed=0
            err=wrap(bearing-self.body)
            self.head=approach(self.head,max(-65,min(65,err)),75*dt)
            # Hysteresis: body follows only significant offsets, then settles within 6°.
            if abs(err)>15 or (self.state=='align' and abs(err)>6):
                self.state='align'; self.stable=0
                step=max(-30*dt,min(30*dt,err)); self.body=wrap(self.body+step)
                self.head=max(-65,min(65,self.head-step))
                self.message='找到人：头部跟随，身体正在对正（模拟）。'
            else:
                self.stable+=dt
                if self.stable>=.5:
                    self.state='dialogue'; self.message='身体已对正，可以对话。'
        else:
            self.stable=0; self.lost+=dt
            if self.state in ('dialogue','align','ambiguous') and self.lost<1:
                self.message='暂时丢失人脸，保持朝向。'; return asdict(self)
            self.state='search'; self.elapsed+=dt; self.scan+=dt
            if self.elapsed>=35:
                self.state='idle'; self.message='我还没看到你，再叫我一声好吗？'; return asdict(self)
            if self.scan<6:  # bounded head sweep first
                target=60*math.sin(self.scan*math.pi/3)
                self.head=approach(self.head,target,65*dt)
                self.message='转头寻找中（模拟）。'
            else:  # stop-and-look, no translation
                phase=(self.scan-6)%3
                if phase<1.5:
                    self.body=wrap(self.body+30*dt)
                    self.message='头部没找到，分段原地转身（模拟）。'
                else: self.message='转身后停稳观察（模拟）。'
                self.head=approach(self.head,0,65*dt)
        return asdict(self)
