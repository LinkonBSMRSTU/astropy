import numpy as np
import matplotlib.pyplot as plt
from astropy import units as u
from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, get_sun

#supress the warning about vector transforms so as not to clutter the doc build log
import warnings
warnings.filterwarnings('ignore',module='astropy.coordinates.baseframe')

m33 = SkyCoord.from_name('M33')
bear_mountain = EarthLocation(lat=41.3*u.deg, lon=-74*u.deg, height=390*u.m)
utcoffset = -4*u.hour  # Eastern Daylight Time
midnight = Time('2012-7-13 00:00:00') - utcoffset

delta_midnight = np.linspace(-12, 12, 1000)*u.hour
times = midnight + delta_midnight
altazframe = AltAz(obstime=times, location=bear_mountain)
sunaltazs = get_sun(times).transform_to(altazframe)
m33altazs = m33.transform_to(altazframe)

plt.plot(delta_midnight, sunaltazs.alt, color='y', label='Sun')
plt.scatter(delta_midnight, m33altazs.alt, c=m33altazs.az, label='M33', lw=0, s=8)
plt.fill_between(delta_midnight, 0, 90, sunaltazs.alt < -0*u.deg, color='0.5', zorder=0)
plt.fill_between(delta_midnight, 0, 90, sunaltazs.alt < -18*u.deg, color='k', zorder=0)
plt.colorbar().set_label('Azimuth [deg]')
plt.legend(loc='upper left')
plt.xlim(-12, 12)
plt.xticks(np.arange(13)*2 -12)
plt.ylim(0, 90)
plt.xlabel('Hours from EDT Midnight')
plt.ylabel('Altitude [deg]')