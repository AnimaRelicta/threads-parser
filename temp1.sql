-- сбросить необработанные профили
UPDATE profiles 
SET processed_mode = NULL 
WHERE about_raw IS NULL


-- пропустить уже обработанные профили
UPDATE profiles 
SET processed_mode = 'profile' 
WHERE about_raw is not NULL


-- обработать все профили из кз как 1 класс
UPDATE profiles 
SET category_id = 1
WHERE "about_location" like '%Казахстан%'


select count(*)
from threads.profiles
where about_raw is not NULL;

select count(*)
from threads.profiles
where about_raw is NULL LIMIT 100


category_id