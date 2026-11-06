-- сбросить необработанные профили
UPDATE profiles 
SET processed_mode = NULL 
WHERE about_raw IS NULL


-- пропустить уже обработанные профили
UPDATE profiles 
SET processed_mode = 'profile' 
WHERE about_raw is not NULL